// Drives bombe_core through every drum position of one wheel order and reports the stops —
// the job of the drum drive and the stop relays in the real machine.
//
// Order: greek (only with greek_en) outermost, then left, middle, right innermost; the same order
// as christopher_core::bombe::run_slice. Stops leave through a valid/ready handshake; the sweep
// waits while a stop is not accepted (the real bombe likewise halted until the operator restarted
// it).

module bombe_sweep
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
  // control
  input  logic                       start,
  output logic                       busy,
  output logic                       done,
  // stops
  output logic                       stop_valid,
  input  logic                       stop_ready,
  output logic [4:0]                 stop_g,
  output logic [4:0]                 stop_l,
  output logic [4:0]                 stop_m,
  output logic [4:0]                 stop_r,
  output logic [25:0]                stop_register,
  output logic [31:0]                positions_tested
);

  logic [4:0] g, l, m, r;
  logic       core_start, core_done, core_stop;
  logic [25:0] core_register;

  bombe_core #(.MAX_EDGES(MAX_EDGES)) u_core (
    .clk          (clk),
    .rst_n        (rst_n),
    .refl_id      (refl_id),
    .greek_en     (greek_en),
    .greek_id     (greek_id),
    .left_id      (left_id),
    .middle_id    (middle_id),
    .right_id     (right_id),
    .edge_en      (edge_en),
    .edge_a       (edge_a),
    .edge_b       (edge_b),
    .edge_pos     (edge_pos),
    .test_letter  (test_letter),
    .input_wire   (input_wire),
    .diagonal_en  (diagonal_en),
    .turnover_en  (turnover_en),
    .turnover_at  (turnover_at),
    .early_exit   (1'b1),
    .pos_g        (g),
    .pos_l        (l),
    .pos_m        (m),
    .pos_r        (r),
    .start        (core_start),
    /* verilator lint_off PINCONNECTEMPTY */
    .busy         (),
    .done         (core_done),
    .stop         (core_stop),
    .test_register(core_register),
    .live         (),
    .iterations   ()
    /* verilator lint_on PINCONNECTEMPTY */
  );

  typedef enum logic [1:0] {IDLE, RUN, EMIT} state_t;
  state_t state;

  // Next drum position, carrying like the drum drive: right -> middle -> left -> greek.
  logic       last;
  logic [4:0] g_n, l_n, m_n, r_n;
  logic       advance;  // leave the current position this cycle

  always_comb begin
    last = (r == 5'd25) && (m == 5'd25) && (l == 5'd25) && (!greek_en || g == 5'd25);
    r_n  = (r == 5'd25) ? 5'd0 : r + 5'd1;
    m_n  = m;
    l_n  = l;
    g_n  = g;
    if (r == 5'd25) begin
      m_n = (m == 5'd25) ? 5'd0 : m + 5'd1;
      if (m == 5'd25) begin
        l_n = (l == 5'd25) ? 5'd0 : l + 5'd1;
        if (l == 5'd25) g_n = g + 5'd1;
      end
    end
    advance = ((state == RUN) && core_done && !core_stop) || ((state == EMIT) && stop_ready);
  end

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      state            <= IDLE;
      done             <= 1'b0;
      core_start       <= 1'b0;
      {g, l, m, r}     <= '0;
      positions_tested <= '0;
    end else begin
      done       <= 1'b0;
      core_start <= 1'b0;
      case (state)
        IDLE: begin
          if (start) begin
            {g, l, m, r}     <= '0;
            positions_tested <= '0;
            core_start       <= 1'b1;
            state            <= RUN;
          end
        end
        RUN: begin
          if (core_done) begin
            positions_tested <= positions_tested + 32'd1;
            if (core_stop) state <= EMIT;
          end
        end
        EMIT: ;
        default: state <= IDLE;
      endcase
      if (advance) begin
        if (last) begin
          state <= IDLE;
          done  <= 1'b1;
        end else begin
          {g, l, m, r} <= {g_n, l_n, m_n, r_n};
          core_start   <= 1'b1;
          state        <= RUN;
        end
      end
    end
  end

  assign busy          = (state != IDLE);
  assign stop_valid    = (state == EMIT);
  assign stop_g        = g;
  assign stop_l        = l;
  assign stop_m        = m;
  assign stop_r        = r;
  assign stop_register = core_register;

endmodule
