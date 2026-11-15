// FPGA bombe engine: sweeps every drum position of one wheel order for one menu, behind the
// register bus of rtl/fpga/README.md.
//
// Unlike the fully parallel reference (rtl/src), it recomputes only what changes — the way the
// real machine's slow drums only move every 26 steps of the fast drum:
//
//   START     load the wiring of the chosen rotors into registers (9 tables x 26 letters)
//   INNER     when (greek, left, middle) change: inner map I = middle..reflector..middle,
//             26 letters through a 7-stage pipeline (and I' with middle+1 for the turnover case)
//   SROWS     the 26 scramblers S_k = right_bwd(k) o I o right_fwd(k), one row (all 26 letters,
//             26 parallel lanes) per cycle, into a small distributed RAM (52 rows with turnover)
//   LATCH     each edge reads its row (r + crib position) mod 26 -> per-edge permutation register
//   SPREAD    676-bit state, one parallel step per clock (edges both ways + diagonal board) until
//             nothing changes or the test register is full -> stop decision
//
// Positions are core offsets at the first crib letter, swept greek(outer) .. right(inner): the
// stop list equals rtl/src/bombe_sweep.sv and christopher_core::bombe::run_slice.

module bombe_engine #(
  parameter int MAX_EDGES = 16,   // <= 32
  parameter int FIFO_LOG2 = 6
) (
  input  logic        clk,
  input  logic        rst_n,
  // register bus (offset within this engine's window)
  input  logic        bus_req,
  input  logic        bus_we,
  input  logic [11:0] bus_addr,
  input  logic [31:0] bus_wdata,
  output logic        bus_ack,
  output logic [31:0] bus_rdata,
  // status for LEDs
  output logic        busy,
  output logic        done_flag,
  output logic        stop_available
);

  localparam logic [25:0] FULL    = '1;
  localparam logic [15:0] VERSION = 16'h0002;  // 2: exhibit/demo registers (PACE, DEMO, snapshot)

  // ---------------------------------------------------------------------------------------------
  // Configuration registers (host must not change them while BUSY)
  // ---------------------------------------------------------------------------------------------
  logic [19:0]           order_q;
  logic [31:0]           options_q;
  logic [31:0]           edge_en_q;
  logic [23:0]           edge_q [MAX_EDGES];

  logic [1:0] refl_id;
  logic       greek_en;
  logic [3:0] greek_id, left_id, middle_id, right_id;
  logic [4:0] test_letter, input_wire;
  logic       diagonal_en, turnover_en;
  logic [7:0] turnover_at;

  assign refl_id     = order_q[1:0];
  assign greek_en    = order_q[2];
  assign greek_id    = order_q[7:4];
  assign left_id     = order_q[11:8];
  assign middle_id   = order_q[15:12];
  assign right_id    = order_q[19:16];
  assign test_letter = options_q[4:0];
  assign input_wire  = options_q[12:8];
  assign diagonal_en = options_q[16];
  assign turnover_en = options_q[17];
  assign turnover_at = options_q[31:24];

  typedef enum logic [3:0] {S_IDLE, S_LOAD, S_INNER, S_SROWS, S_LATCH, S_SPREAD, S_PUSH, S_PACE, S_ADVANCE} state_t;
  state_t state;

  // ---------------------------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------------------------
  // Entry c of a packed 26 x 5-bit table.
  function automatic logic [4:0] pick(input logic [129:0] t, input logic [4:0] c);
    return t[c*5 +: 5];
  endfunction

  // ---------------------------------------------------------------------------------------------
  // Wiring tables of the selected rotors: 0 R fwd, 1 R bwd, 2 M fwd, 3 M bwd, 4 L fwd, 5 L bwd,
  // 6 G fwd, 7 G bwd, 8 reflector. Loaded one entry per cycle at START.
  // ---------------------------------------------------------------------------------------------
  logic [129:0] tbl [9];
  logic [3:0]   ld_t;
  logic [4:0]   ld_c;
  logic [3:0]   ld_id;

  always_comb begin
    case (ld_t)
      4'd0, 4'd1: ld_id = right_id;
      4'd2, 4'd3: ld_id = middle_id;
      4'd4, 4'd5: ld_id = left_id;
      4'd6, 4'd7: ld_id = greek_id;
      4'd8:       ld_id = {2'b00, refl_id};
      default:    ld_id = 4'd0;
    endcase
    if (ld_id > 4'd9) ld_id = 4'd0;  // unused greek slot may hold junk
  end

  // Two-stage loader (off the timing-critical path): stage 1 computes the bit index into the
  // constant tables, stage 2 reads the entry and writes it into tbl[t][c]. The last entry lands
  // two cycles after LOAD ends, long before INNER/SROWS read those tables.
  logic        ld_v1;
  logic [1:0]  ld_src1;   // 0 rotor fwd, 1 rotor bwd, 2 reflector
  logic [3:0]  ld_t1;
  logic [4:0]  ld_c1;
  logic [10:0] ld_idx1;
  logic [4:0]  ld_val;

  always_ff @(posedge clk) begin
    ld_v1   <= (state == S_LOAD);
    ld_t1   <= ld_t;
    ld_c1   <= ld_c;
    ld_src1 <= (ld_t == 4'd8) ? 2'd2 : {1'b0, ld_t[0]};
    ld_idx1 <= ({7'd0, ld_id} * 11'd26 + {6'd0, ld_c}) * 11'd5;
    if (ld_v1) tbl[ld_t1][ld_c1*5 +: 5] <= ld_val;
  end

  always_comb begin
    case (ld_src1)
      2'd0:    ld_val = wirings_pkg::ROTOR_FWD[ld_idx1 +: 5];
      2'd1:    ld_val = wirings_pkg::ROTOR_BWD[ld_idx1 +: 5];
      default: ld_val = wirings_pkg::REFLECTOR[ld_idx1[9:0] +: 5];  // 4 x 26 x 5 = 520 bits
    endcase
  end

  // ---------------------------------------------------------------------------------------------
  // Drum position
  // ---------------------------------------------------------------------------------------------
  logic [4:0] g, l, m, r;
  logic [4:0] m1_q;  // middle core + 1 (registered; m changes at least a cycle before it is used)
  always_ff @(posedge clk) m1_q <= wirings_pkg::add26(m, 5'd1);

  // ---------------------------------------------------------------------------------------------
  // Inner maps I (middle core m) and I' (middle core m+1), one letter per cycle. Every rotor
  // passage takes two stages (index = letter + core; then table lookup - core) to keep each
  // stage short: 13 stages after the input register.
  // ---------------------------------------------------------------------------------------------
  localparam int IN_ST = 14;
  logic         in_feed;          // feed letter in_x this cycle
  logic [4:0]   in_x;
  logic [IN_ST-1:0] in_valid;     // stage valid bits (stage 0 = input register)
  logic [4:0]   in_xs [IN_ST];
  logic [4:0]   in_va [IN_ST];
  logic [4:0]   in_vb [IN_ST];
  logic [129:0] inner0, inner1;   // I and I' as packed tables

  always_ff @(posedge clk) begin
    in_valid <= {in_valid[IN_ST-2:0], in_feed};
    in_xs[0] <= in_x;
    in_va[0] <= in_x;
    in_vb[0] <= in_x;
    for (int s = 1; s < IN_ST; s++) in_xs[s] <= in_xs[s-1];
    // middle forward
    in_va[1]  <= wirings_pkg::add26(in_va[0], m);
    in_vb[1]  <= wirings_pkg::add26(in_vb[0], m1_q);
    in_va[2]  <= wirings_pkg::sub26(pick(tbl[2], in_va[1]), m);
    in_vb[2]  <= wirings_pkg::sub26(pick(tbl[2], in_vb[1]), m1_q);
    // left forward
    in_va[3]  <= wirings_pkg::add26(in_va[2], l);
    in_vb[3]  <= wirings_pkg::add26(in_vb[2], l);
    in_va[4]  <= wirings_pkg::sub26(pick(tbl[4], in_va[3]), l);
    in_vb[4]  <= wirings_pkg::sub26(pick(tbl[4], in_vb[3]), l);
    // greek forward (pass-through without a greek wheel)
    in_va[5]  <= greek_en ? wirings_pkg::add26(in_va[4], g) : in_va[4];
    in_vb[5]  <= greek_en ? wirings_pkg::add26(in_vb[4], g) : in_vb[4];
    in_va[6]  <= greek_en ? wirings_pkg::sub26(pick(tbl[6], in_va[5]), g) : in_va[5];
    in_vb[6]  <= greek_en ? wirings_pkg::sub26(pick(tbl[6], in_vb[5]), g) : in_vb[5];
    // reflector
    in_va[7]  <= pick(tbl[8], in_va[6]);
    in_vb[7]  <= pick(tbl[8], in_vb[6]);
    // greek backward
    in_va[8]  <= greek_en ? wirings_pkg::add26(in_va[7], g) : in_va[7];
    in_vb[8]  <= greek_en ? wirings_pkg::add26(in_vb[7], g) : in_vb[7];
    in_va[9]  <= greek_en ? wirings_pkg::sub26(pick(tbl[7], in_va[8]), g) : in_va[8];
    in_vb[9]  <= greek_en ? wirings_pkg::sub26(pick(tbl[7], in_vb[8]), g) : in_vb[8];
    // left backward
    in_va[10] <= wirings_pkg::add26(in_va[9], l);
    in_vb[10] <= wirings_pkg::add26(in_vb[9], l);
    in_va[11] <= wirings_pkg::sub26(pick(tbl[5], in_va[10]), l);
    in_vb[11] <= wirings_pkg::sub26(pick(tbl[5], in_vb[10]), l);
    // middle backward
    in_va[12] <= wirings_pkg::add26(in_va[11], m);
    in_vb[12] <= wirings_pkg::add26(in_vb[11], m1_q);
    in_va[13] <= wirings_pkg::sub26(pick(tbl[3], in_va[12]), m);
    in_vb[13] <= wirings_pkg::sub26(pick(tbl[3], in_vb[12]), m1_q);
    if (in_valid[IN_ST-1]) begin
      inner0[in_xs[IN_ST-1]*5 +: 5] <= in_va[IN_ST-1];
      inner1[in_xs[IN_ST-1]*5 +: 5] <= in_vb[IN_ST-1];
    end
  end

  // ---------------------------------------------------------------------------------------------
  // Scrambler rows: row k (k = right core) = S_k for all 26 input letters; rows 26..51 use I'.
  // 26 parallel lanes, one row per cycle, 5 stages:
  //   1 index x+k   2 right fwd lookup - k   3 inner map   4 index +k   5 right bwd lookup - k
  // ---------------------------------------------------------------------------------------------
  localparam int SR_ST = 5;
  logic             sr_feed;
  logic [5:0]       sr_row;         // 0..51
  logic [SR_ST-1:0] sr_valid;
  logic [5:0]       sr_rows [SR_ST];
  logic [4:0]       sr_k    [SR_ST];
  logic [129:0]     sr_i1, sr_a, sr_b, sr_i4, sr_c;
  logic [4:0]       sr_k_in;
  assign sr_k_in = (sr_row >= 6'd26) ? 5'(sr_row - 6'd26) : sr_row[4:0];

  always_ff @(posedge clk) begin
    sr_valid   <= {sr_valid[SR_ST-2:0], sr_feed};
    sr_rows[0] <= sr_row;
    sr_k[0]    <= sr_k_in;
    for (int s = 1; s < SR_ST; s++) begin
      sr_rows[s] <= sr_rows[s-1];
      sr_k[s]    <= sr_k[s-1];
    end
    for (int x = 0; x < 26; x++) begin
      sr_i1[x*5 +: 5] <= wirings_pkg::add26(5'(x), sr_k_in);
      sr_a[x*5 +: 5]  <= wirings_pkg::sub26(pick(tbl[0], sr_i1[x*5 +: 5]), sr_k[0]);
      sr_b[x*5 +: 5]  <= (sr_rows[1] >= 6'd26) ? pick(inner1, sr_a[x*5 +: 5]) : pick(inner0, sr_a[x*5 +: 5]);
      sr_i4[x*5 +: 5] <= wirings_pkg::add26(sr_b[x*5 +: 5], sr_k[2]);
      sr_c[x*5 +: 5]  <= wirings_pkg::sub26(pick(tbl[1], sr_i4[x*5 +: 5]), sr_k[3]);
    end
  end

  // ---------------------------------------------------------------------------------------------
  // Per-edge permutations for the current position. Every edge has its own copy of the row table
  // (one write port shared, one read port each) so it maps onto distributed RAM.
  // ---------------------------------------------------------------------------------------------
  logic [4:0]               posm   [MAX_EDGES];  // crib position mod 26 (latched at START)
  logic [MAX_EDGES-1:0]     turn_e;               // edge uses the turned-over middle rotor
  logic [MAX_EDGES*130-1:0] perm_q;
  logic                     latch_perm;

  for (genvar e = 0; e < MAX_EDGES; e++) begin : g_rows
    (* ram_style = "distributed" *) logic [129:0] rows [64];
    logic [5:0] rd_addr;
    assign rd_addr = {1'b0, wirings_pkg::add26(r, posm[e])} + (turn_e[e] ? 6'd26 : 6'd0);
    always_ff @(posedge clk) begin
      if (sr_valid[SR_ST-1]) rows[sr_rows[SR_ST-1]] <= sr_c;
      if (latch_perm)  perm_q[e*130 +: 130] <= rows[rd_addr];
    end
  end

  // ---------------------------------------------------------------------------------------------
  // Spreading (same rules as rtl/src/bombe_core.sv), organised by cable for hardware:
  // pick the two cables of each edge (26:1 of 26-bit words), permute (26:1 per wire), and OR the
  // results into the destination cables through one-hot decodes made at START.
  // ---------------------------------------------------------------------------------------------
  logic [25:0] cab_q [26];          // cable c, bit w = wire w live
  logic [25:0] cab_n [26];
  logic [25:0] cab_prev [26];       // state one step earlier (fixed-point test on registers)
  logic [25:0] dec_a [MAX_EDGES];   // one-hot plain letter of edge i (0 if disabled)
  logic [25:0] dec_b [MAX_EDGES];   // one-hot cipher letter of edge i (0 if disabled)
  logic [25:0] va [MAX_EDGES], vb [MAX_EDGES];
  logic [25:0] to_b [MAX_EDGES], to_a [MAX_EDGES];

  always_comb begin
    for (int i = 0; i < MAX_EDGES; i++) begin
      va[i] = cab_q[edge_q[i][4:0]];
      vb[i] = cab_q[edge_q[i][12:8]];
      for (int x = 0; x < 26; x++) begin
        // wire y of one end reaches wire perm[y] of the other; perm is an involution, so wire x
        // of the far end is fed by wire perm[x] of the near end.
        to_b[i][x] = va[i][perm_q[(i*26 + x)*5 +: 5]];
        to_a[i][x] = vb[i][perm_q[(i*26 + x)*5 +: 5]];
      end
    end
    for (int c = 0; c < 26; c++) begin
      cab_n[c] = cab_q[c];
      for (int i = 0; i < MAX_EDGES; i++) begin
        if (dec_b[i][c]) cab_n[c] = cab_n[c] | to_b[i];
        if (dec_a[i][c]) cab_n[c] = cab_n[c] | to_a[i];
      end
      if (diagonal_en) begin
        for (int w = 0; w < 26; w++) cab_n[c][w] = cab_n[c][w] | cab_q[w][c];
      end
    end
  end

  logic [25:0] test_q;
  logic        stable, test_full;
  assign test_q    = cab_q[test_letter];
  assign test_full = (test_q == FULL);
  always_comb begin
    stable = 1'b1;
    for (int c = 0; c < 26; c++) stable = stable & (cab_q[c] == cab_prev[c]);
  end

  // ---------------------------------------------------------------------------------------------
  // Stop FIFO: {g, l, m, r, test register}
  // ---------------------------------------------------------------------------------------------
  localparam int DEPTH = 1 << FIFO_LOG2;
  logic [45:0]        fifo [DEPTH];
  logic [FIFO_LOG2:0] wr_ptr, rd_ptr;
  logic               fifo_full, fifo_empty, push;
  assign fifo_empty = (wr_ptr == rd_ptr);
  assign fifo_full  = (wr_ptr[FIFO_LOG2] != rd_ptr[FIFO_LOG2]) &&
                      (wr_ptr[FIFO_LOG2-1:0] == rd_ptr[FIFO_LOG2-1:0]);

  always_ff @(posedge clk) begin
    if (push) fifo[wr_ptr[FIFO_LOG2-1:0]] <= {g, l, m, r, test_q};
  end

  // ---------------------------------------------------------------------------------------------
  // Control
  // ---------------------------------------------------------------------------------------------
  logic        start_pulse, abort_pulse, step_pulse;
  // Exhibit/demo controls (README "Exhibit mode"); all-zero = full speed, behaviour unchanged.
  logic [31:0] pace_q;        // clock cycles to wait after every position (0 = no wait)
  logic        single_q;      // DEMO[0]: hold after every position until a STEP write
  logic        pause_stop_q;  // DEMO[1]: hold after a stop position until a STEP write
  logic [31:0] pace_cnt;
  logic        stop_cur;      // the position just decided was a stop (not a full test register)
  logic        hold_now;      // PACE state is waiting for STEP rather than for the pace counter
  logic [15:0] iters_q;       // propagation steps of the position in progress
  logic [25:0] snap_q [26];   // cable state latched at the end of the last position
  logic [19:0] snap_pos_q;    // {g, l, m, r} of the snapshot
  logic [15:0] snap_iters_q;
  logic        snap_valid_q, snap_stop_q;
  logic        done_q, overflow_q;
  logic [31:0] positions_q, cycles_q;
  logic [5:0]  wait_cnt;
  logic        last;

  assign hold_now = single_q || (pause_stop_q && stop_cur);
  assign last = (r == 5'd25) && (m == 5'd25) && (l == 5'd25) && (!greek_en || g == 5'd25);

  always_comb begin
    in_feed    = (state == S_INNER) && (in_x <= 5'd25) && (wait_cnt == 6'd0);
    sr_feed    = (state == S_SROWS) && (wait_cnt == 6'd0) && (sr_row <= (turnover_en ? 6'd51 : 6'd25));
    latch_perm = (state == S_LATCH);
    push       = (state == S_PUSH) && !fifo_full;
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state       <= S_IDLE;
      done_q      <= 1'b0;
      overflow_q  <= 1'b0;
      positions_q <= '0;
      cycles_q    <= '0;
      {g, l, m, r} <= '0;
      ld_t        <= '0;
      ld_c        <= '0;
      in_x        <= '0;
      sr_row      <= '0;
      wait_cnt    <= '0;
      for (int c = 0; c < 26; c++) begin
        cab_q[c]    <= '0;
        cab_prev[c] <= '0;
      end
      wr_ptr      <= '0;
      pace_cnt     <= '0;
      stop_cur     <= 1'b0;
      iters_q      <= '0;
      snap_pos_q   <= '0;
      snap_iters_q <= '0;
      snap_valid_q <= 1'b0;
      snap_stop_q  <= 1'b0;
    end else begin
      if (state != S_IDLE) cycles_q <= cycles_q + 32'd1;
      case (state)
        S_IDLE: begin
          if (start_pulse) begin
            done_q      <= 1'b0;
            overflow_q  <= 1'b0;
            positions_q <= '0;
            cycles_q    <= '0;
            {g, l, m, r} <= '0;
            ld_t        <= '0;
            ld_c        <= '0;
            wr_ptr      <= rd_ptr;  // discard stops of an earlier sweep
            snap_valid_q <= 1'b0;
            state       <= S_LOAD;
          end
        end
        S_LOAD: begin
          if (ld_c == 5'd25) begin
            ld_c <= '0;
            if (ld_t == 4'd8) begin
              in_x     <= '0;
              wait_cnt <= '0;
              state    <= S_INNER;
            end else begin
              ld_t <= ld_t + 4'd1;
            end
          end else begin
            ld_c <= ld_c + 5'd1;
          end
        end
        S_INNER: begin
          // Feed 26 letters, then wait for the pipeline to drain.
          if (in_x <= 5'd25) begin
            in_x <= in_x + 5'd1;
          end else if (wait_cnt == 6'(IN_ST)) begin
            wait_cnt <= '0;
            sr_row   <= '0;
            state    <= S_SROWS;
          end else begin
            wait_cnt <= wait_cnt + 6'd1;
          end
        end
        S_SROWS: begin
          if (sr_row <= (turnover_en ? 6'd51 : 6'd25)) begin
            sr_row <= sr_row + 6'd1;
          end else if (wait_cnt == 6'(SR_ST)) begin
            wait_cnt <= '0;
            state    <= S_LATCH;
          end else begin
            wait_cnt <= wait_cnt + 6'd1;
          end
        end
        S_LATCH: begin
          // perm_q latches this cycle; seed the test register.
          for (int c = 0; c < 26; c++) begin
            cab_q[c]    <= '0;
            cab_prev[c] <= '1;  // differs from any reachable state: no false "stable"
          end
          cab_q[test_letter][input_wire] <= 1'b1;
          iters_q <= '0;
          state <= S_SPREAD;
        end
        S_SPREAD: begin
          // Decide on registered state: stable (fixed point) or test register full.
          if (stable || test_full) begin
            positions_q <= positions_q + 32'd1;
            // Snapshot for the exhibit display (plain registers, no logic on the way in).
            snap_q       <= cab_q;
            snap_pos_q   <= {g, l, m, r};
            snap_iters_q <= iters_q;
            snap_valid_q <= 1'b1;
            snap_stop_q  <= !test_full;
            stop_cur     <= !test_full;
            pace_cnt     <= '0;
            if (test_full)
              state <= (pace_q != 32'd0 || single_q) ? S_PACE : S_ADVANCE;
            else
              state <= S_PUSH;
          end else begin
            cab_prev <= cab_q;
            cab_q    <= cab_n;
            if (iters_q != 16'hFFFF) iters_q <= iters_q + 16'd1;
          end
        end
        S_PUSH: begin
          if (!fifo_full) begin
            wr_ptr <= wr_ptr + 1'b1;
            state  <= (pace_q != 32'd0 || hold_now) ? S_PACE : S_ADVANCE;
          end
        end
        S_PACE: begin
          // Exhibit mode: slow the sweep to human speed or wait for a STEP write.
          if (hold_now) begin
            if (step_pulse) state <= S_ADVANCE;
          end else if (pace_cnt >= pace_q) begin
            state <= S_ADVANCE;
          end else begin
            pace_cnt <= pace_cnt + 32'd1;
          end
        end
        S_ADVANCE: begin
          if (last) begin
            done_q <= 1'b1;
            state  <= S_IDLE;
          end else if (r != 5'd25) begin
            r     <= r + 5'd1;
            state <= S_LATCH;
          end else begin
            r <= '0;
            if (m != 5'd25) m <= m + 5'd1;
            else begin
              m <= '0;
              if (l != 5'd25) l <= l + 5'd1;
              else begin
                l <= '0;
                g <= g + 5'd1;
              end
            end
            in_x     <= '0;
            wait_cnt <= '0;
            state    <= S_INNER;
          end
        end
        default: state <= S_IDLE;
      endcase
      if (abort_pulse) state <= S_IDLE;
    end
  end

  // Static per-run edge data (config is stable while busy).
  always_ff @(posedge clk) begin
    if (state == S_IDLE && start_pulse) begin
      for (int e = 0; e < MAX_EDGES; e++) begin
        posm[e]   <= 5'(edge_q[e][23:16] % 8'd26);
        turn_e[e] <= turnover_en && (edge_q[e][23:16] >= turnover_at);
        dec_a[e]  <= edge_en_q[e] ? (26'd1 << edge_q[e][4:0]) : 26'd0;
        dec_b[e]  <= edge_en_q[e] ? (26'd1 << edge_q[e][12:8]) : 26'd0;
      end
    end
  end

  // ---------------------------------------------------------------------------------------------
  // Register bus
  // ---------------------------------------------------------------------------------------------
  // Edge register index (0x100 + 4*i).
  localparam int EIW = (MAX_EDGES > 1) ? $clog2(MAX_EDGES) : 1;
  logic       edge_sel;
  logic [EIW-1:0] eidx;
  assign edge_sel = (bus_addr[11:8] == 4'h1) && ({1'b0, bus_addr[7:2]} < 7'(MAX_EDGES));
  assign eidx     = bus_addr[EIW+1:2];
  logic snap_sel;                                  // 0x200 + 4*cable, cable 0..25
  assign snap_sel = (bus_addr[11:7] == 5'b00100) && (bus_addr[6:2] < 5'd26);

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      bus_ack     <= 1'b0;
      bus_rdata   <= '0;
      start_pulse <= 1'b0;
      abort_pulse <= 1'b0;
      step_pulse  <= 1'b0;
      pace_q       <= '0;
      single_q     <= 1'b0;
      pause_stop_q <= 1'b0;
      order_q     <= '0;
      options_q   <= '0;
      edge_en_q   <= '0;
      rd_ptr      <= '0;
      for (int e = 0; e < MAX_EDGES; e++) edge_q[e] <= '0;
    end else begin
      bus_ack     <= bus_req;
      start_pulse <= 1'b0;
      abort_pulse <= 1'b0;
      step_pulse  <= 1'b0;
      if (bus_req && bus_we) begin
        case (bus_addr)
          12'h000: begin
            start_pulse <= bus_wdata[0];
            abort_pulse <= bus_wdata[1];
            step_pulse  <= bus_wdata[2];
          end
          12'h028: pace_q <= bus_wdata;
          12'h02C: begin
            single_q     <= bus_wdata[0];
            pause_stop_q <= bus_wdata[1];
          end
          12'h008: order_q   <= bus_wdata[19:0];
          12'h00C: options_q <= bus_wdata;
          12'h010: edge_en_q <= bus_wdata;
          default: begin
            if (edge_sel) edge_q[eidx] <= bus_wdata[23:0];
          end
        endcase
      end
      if (bus_req && !bus_we) begin
        case (bus_addr)
          12'h004: bus_rdata <= {27'd0, (state == S_PACE) && hold_now, overflow_q, !fifo_empty, done_q, busy};
          12'h008: bus_rdata <= {12'd0, order_q};
          12'h00C: bus_rdata <= options_q;
          12'h010: bus_rdata <= edge_en_q;
          12'h014: bus_rdata <= positions_q;
          12'h018: bus_rdata <= cycles_q;
          12'h01C: bus_rdata <= fifo_empty ? 32'd0
                                           : {1'b1, 6'd0, fifo[rd_ptr[FIFO_LOG2-1:0]][45:26], 5'd0};
          12'h020: begin
            bus_rdata <= fifo_empty ? 32'd0 : {6'd0, fifo[rd_ptr[FIFO_LOG2-1:0]][25:0]};
            if (!fifo_empty) rd_ptr <= rd_ptr + 1'b1;
          end
          12'h024: bus_rdata <= {VERSION, 8'(FIFO_LOG2), 8'(MAX_EDGES)};
          12'h028: bus_rdata <= pace_q;
          12'h02C: bus_rdata <= {30'd0, pause_stop_q, single_q};
          12'h030: bus_rdata <= {7'd0, g, l, m, r, 5'd0};
          12'h034: bus_rdata <= {6'd0, test_q};
          12'h038: bus_rdata <= {snap_valid_q, snap_stop_q, 5'd0, snap_pos_q, 5'd0};
          12'h03C: bus_rdata <= {16'd0, snap_iters_q};
          default: begin
            if (edge_sel)
              bus_rdata <= {8'd0, edge_q[eidx]};
            else if (snap_sel)
              bus_rdata <= {6'd0, snap_q[bus_addr[6:2]]};   // bus-side read mux only
            else
              bus_rdata <= 32'd0;
          end
        endcase
      end
    end
  end

  assign busy           = (state != S_IDLE);
  assign done_flag      = done_q;
  assign stop_available = !fifo_empty;

endmodule
