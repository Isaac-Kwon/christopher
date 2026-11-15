// N bombe engines behind one register bus (rtl/fpga/README.md):
// bus_addr[15:12] = engine index (0x0..0xE), 0xF = global block (ID, INFO).
// Like a bombe hut: several machines, each running its own wheel order.

module bombe_array #(
  parameter int N_ENGINES = 1,    // 1..15
  parameter int MAX_EDGES = 16,
  parameter int FIFO_LOG2 = 6,
  parameter int CLK_MHZ   = 100
) (
  input  logic                 clk,
  input  logic                 rst_n,
  input  logic                 bus_req,
  input  logic                 bus_we,
  input  logic [15:0]          bus_addr,
  input  logic [31:0]          bus_wdata,
  output logic                 bus_ack,
  output logic [31:0]          bus_rdata,
  output logic [N_ENGINES-1:0] busy,
  output logic [N_ENGINES-1:0] done,
  output logic [N_ENGINES-1:0] stop_available
);

  localparam logic [31:0] ID = 32'h424F_4D42;  // "BOMB"

  logic [3:0]  sel;
  assign sel = bus_addr[15:12];

  logic [N_ENGINES-1:0] e_ack;
  logic [31:0]          e_rdata [N_ENGINES];

  for (genvar k = 0; k < N_ENGINES; k++) begin : g_engine
    bombe_engine #(.MAX_EDGES(MAX_EDGES), .FIFO_LOG2(FIFO_LOG2)) u_engine (
      .clk           (clk),
      .rst_n         (rst_n),
      .bus_req       (bus_req && sel == 4'(k)),
      .bus_we        (bus_we),
      .bus_addr      (bus_addr[11:0]),
      .bus_wdata     (bus_wdata),
      .bus_ack       (e_ack[k]),
      .bus_rdata     (e_rdata[k]),
      .busy          (busy[k]),
      .done_flag     (done[k]),
      .stop_available(stop_available[k])
    );
  end

  // Global block and requests to absent engines answer after one cycle as well.
  logic        g_ack;
  logic [31:0] g_rdata;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      g_ack   <= 1'b0;
      g_rdata <= '0;
    end else begin
      g_ack <= bus_req && (sel == 4'hF || int'(sel) >= N_ENGINES);
      case (bus_addr)
        16'hF000: g_rdata <= ID;
        16'hF004: g_rdata <= {16'(CLK_MHZ), 8'(MAX_EDGES), 8'(N_ENGINES)};
        default:  g_rdata <= 32'd0;
      endcase
    end
  end

  always_comb begin
    bus_ack   = g_ack | (|e_ack);
    bus_rdata = g_ack ? g_rdata : 32'd0;
    for (int k = 0; k < N_ENGINES; k++) begin
      if (e_ack[k]) bus_rdata = e_rdata[k];
    end
  end

endmodule
