// One bombe position: the menu wired with one scrambler per edge, the diagonal board, and the
// test register. See rtl/README.md for the port contract.
//
// The real bombe's network settles electrically at once; a direct combinational copy would be a
// combinational loop. Here the 676-bit state (26 cables x 26 wires) is a register and every
// clock performs one parallel spreading step over all edges (both directions) and the diagonal
// board, until nothing changes. The fixed point is the same set of live wires the Rust model
// (christopher_core::bombe::Plugged::propagate) reaches with its worklist.

module bombe_core
#(
  parameter int MAX_EDGES = 16
) (
  input  logic                       clk,
  input  logic                       rst_n,
  // wheel order
  input  logic [1:0]                 refl_id,
  input  logic                       greek_en,
  input  logic [3:0]                 greek_id,
  input  logic [3:0]                 left_id,
  input  logic [3:0]                 middle_id,
  input  logic [3:0]                 right_id,
  // menu
  input  logic [MAX_EDGES-1:0]       edge_en,
  input  logic [MAX_EDGES-1:0][4:0]  edge_a,
  input  logic [MAX_EDGES-1:0][4:0]  edge_b,
  input  logic [MAX_EDGES-1:0][7:0]  edge_pos,
  input  logic [4:0]                 test_letter,
  input  logic [4:0]                 input_wire,
  input  logic                       diagonal_en,
  input  logic                       turnover_en,
  input  logic [7:0]                 turnover_at,
  input  logic                       early_exit,
  // drum position (core offsets at the first crib letter)
  input  logic [4:0]                 pos_g,
  input  logic [4:0]                 pos_l,
  input  logic [4:0]                 pos_m,
  input  logic [4:0]                 pos_r,
  // control
  input  logic                       start,
  output logic                       busy,
  output logic                       done,
  output logic                       stop,
  output logic [25:0]                test_register,
  output logic [26*26-1:0]           live,
  output logic [15:0]                iterations
);

  localparam logic [25:0] FULL = '1;

  // ---------------------------------------------------------------------------------------------
  // Scramblers: one per edge, right drum advanced by the edge's crib position.
  // ---------------------------------------------------------------------------------------------
  // Flat vectors (edge e, letter c at bits (e*26 + c)*5 +: 5) — portable across Verilator, Icarus
  // and Yosys, which differ in support for variable indices into multi-dimensional arrays.
  logic [MAX_EDGES*26*5-1:0] perm_c;  // from the current inputs
  logic [MAX_EDGES*26*5-1:0] perm_q;  // latched at start

  for (genvar e = 0; e < MAX_EDGES; e++) begin : g_edge
    logic [8:0] sum_r;
    /* verilator lint_off UNUSEDSIGNAL */
    logic [8:0] mod_r;  // < 26, only the low 5 bits are used
    /* verilator lint_on UNUSEDSIGNAL */
    logic [4:0] core_r_e;
    logic [4:0] core_m_e;
    always_comb begin
      sum_r    = {4'd0, pos_r} + {1'b0, edge_pos[e]};
      mod_r    = sum_r % 9'd26;
      core_r_e = mod_r[4:0];
      core_m_e = (turnover_en && edge_pos[e] >= turnover_at) ? wirings_pkg::add26(pos_m, 5'd1) : pos_m;
    end
    scrambler u_scrambler (
      .refl_id  (refl_id),
      .greek_en (greek_en),
      .greek_id (greek_id),
      .left_id  (left_id),
      .middle_id(middle_id),
      .right_id (right_id),
      .core_g   (pos_g),
      .core_l   (pos_l),
      .core_m   (core_m_e),
      .core_r   (core_r_e),
      .perm     (perm_c[e*130 +: 130])
    );
  end

  // ---------------------------------------------------------------------------------------------
  // Spreading step: live_n = live_q plus everything one step away.
  // ---------------------------------------------------------------------------------------------
  // Cable c, wire w at bit c*26 + w.
  logic [26*26-1:0] live_q;
  logic [26*26-1:0] live_n;
  int               cable_a, cable_b;
  logic [31:0]      y;

  always_comb begin
    live_n = live_q;
    y      = '0;
    for (int i = 0; i < MAX_EDGES; i++) begin
      cable_a = 26 * edge_a[i];
      cable_b = 26 * edge_b[i];
      if (edge_en[i]) begin
        for (int x = 0; x < 26; x++) begin
          // Wire y of one cable reaches wire perm[y] of the other; perm is an involution, so wire
          // x of the far cable is fed by wire y = perm[x] of the near one.
          y = {27'd0, perm_q[(i*26 + x)*5 +: 5]};
          if (live_q[cable_a + y]) live_n[cable_b + x] = 1'b1;
          if (live_q[cable_b + y]) live_n[cable_a + x] = 1'b1;
        end
      end
    end
    if (diagonal_en) begin
      // Diagonal board: wire w of cable c <-> wire c of cable w.
      for (int c = 0; c < 26; c++) begin
        for (int w = 0; w < 26; w++) begin
          if (live_q[c*26 + w]) live_n[w*26 + c] = 1'b1;
        end
      end
    end
  end

  logic [25:0] test_q, test_n;
  assign test_q = live_q[26*test_letter +: 26];
  assign test_n = live_n[26*test_letter +: 26];

  // ---------------------------------------------------------------------------------------------
  // Control
  // ---------------------------------------------------------------------------------------------
  typedef enum logic {IDLE, SPREAD} state_t;
  state_t state;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state      <= IDLE;
      done       <= 1'b0;
      live_q     <= '0;
      perm_q     <= '0;
      iterations <= '0;
    end else begin
      done <= 1'b0;
      case (state)
        IDLE: begin
          if (start) begin
            perm_q     <= perm_c;
            live_q     <= '0;
            live_q[26*test_letter + input_wire] <= 1'b1;
            iterations <= '0;
            state      <= SPREAD;
          end
        end
        SPREAD: begin
          live_q     <= live_n;
          iterations <= iterations + 16'd1;
          if (live_n == live_q || (early_exit && test_n == FULL)) begin
            state <= IDLE;
            done  <= 1'b1;
          end
        end
        default: state <= IDLE;
      endcase
    end
  end

  assign busy          = (state != IDLE);
  assign test_register = test_q;
  assign stop          = (test_q != FULL);
  assign live          = live_q;

endmodule
