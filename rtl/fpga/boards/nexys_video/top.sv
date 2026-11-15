// Nexys Video top: UART <-> register bus <-> bombe_array.
//
// Clock: the 100 MHz board oscillator feeds an MMCM that makes the 50 MHz system clock. The
// engine's spreading step (one parallel step over all menu edges + diagonal board) is ~15 ns after
// place and route on this -1 part, so 100 MHz does not close timing; 50 MHz leaves margin and is
// still far faster than needed (the FPGA is the electronic exhibit and the reference for the
// mechanical and relay machines, not a throughput race).
//
// LEDs: [0] heartbeat  [1] reset released (MMCM locked)  [2] engine0 busy  [3] engine0 done
//       [4] engine0 stop available  [5] UART activity (stretched)  [6],[7] unused (0)
module top #(
  parameter int N_ENGINES = 1,
  parameter int MAX_EDGES = 16,
  parameter int FIFO_LOG2 = 6,
  parameter int BAUD      = 921_600
) (
  input  logic       sys_clk,       // 100 MHz
  input  logic       cpu_resetn,    // button, active low
  input  logic       uart_tx_in,    // FTDI TX -> FPGA
  output logic       uart_rx_out,   // FPGA -> FTDI RX
  output logic [7:0] led
);
  localparam int CLK_HZ = 50_000_000;

  // ---------------------------------------------------------------------------------------------
  // 100 MHz -> 50 MHz (VCO 1000 MHz = 100 MHz x 10, output / 20)
  // ---------------------------------------------------------------------------------------------
  logic clk, locked;
`ifdef VERILATOR
  assign clk    = sys_clk;
  assign locked = 1'b1;
`else
  logic clk_mmcm, clk_fb;
  MMCME2_BASE #(
    .CLKIN1_PERIOD   (10.0),
    .DIVCLK_DIVIDE   (1),
    .CLKFBOUT_MULT_F (10.0),
    .CLKOUT0_DIVIDE_F(20.0)
  ) u_mmcm (
    .CLKIN1   (sys_clk),
    .CLKFBIN  (clk_fb),
    .CLKFBOUT (clk_fb),
    .CLKOUT0  (clk_mmcm),
    .LOCKED   (locked),
    .RST      (1'b0),
    .PWRDWN   (1'b0),
    /* verilator lint_off PINCONNECTEMPTY */
    .CLKFBOUTB(), .CLKOUT0B(), .CLKOUT1(), .CLKOUT1B(), .CLKOUT2(), .CLKOUT2B(),
    .CLKOUT3(), .CLKOUT3B(), .CLKOUT4(), .CLKOUT5(), .CLKOUT6()
    /* verilator lint_on PINCONNECTEMPTY */
  );
  BUFG u_bufg (.I(clk_mmcm), .O(clk));
`endif

  // Reset synchroniser: asserted asynchronously (button or MMCM not locked), released
  // synchronously after 16 clocks.
  logic [3:0] rst_pipe = '0;
  logic       rst_n;
  logic       rst_in_n;
  assign rst_in_n = cpu_resetn & locked;
  always_ff @(posedge clk or negedge rst_in_n)
    if (!rst_in_n) rst_pipe <= '0;
    else           rst_pipe <= {rst_pipe[2:0], 1'b1};
  assign rst_n = rst_pipe[3];

  logic        bus_req, bus_we, bus_ack;
  logic [15:0] bus_addr;
  logic [31:0] bus_wdata, bus_rdata;

  uart_bridge #(.CLK_HZ(CLK_HZ), .BAUD(BAUD)) u_uart (
    .clk(clk), .rst_n(rst_n), .rxd(uart_tx_in), .txd(uart_rx_out),
    .bus_req, .bus_we, .bus_addr, .bus_wdata, .bus_ack, .bus_rdata
  );

  logic [N_ENGINES-1:0] busy, done, stop_available;

  bombe_array #(.N_ENGINES(N_ENGINES), .MAX_EDGES(MAX_EDGES), .FIFO_LOG2(FIFO_LOG2),
                .CLK_MHZ(CLK_HZ / 1_000_000)) u_array (
    .clk(clk), .rst_n(rst_n),
    .bus_req, .bus_we, .bus_addr, .bus_wdata, .bus_ack, .bus_rdata,
    .busy, .done, .stop_available
  );

  logic [25:0] hb = '0;   // bit 25 toggles every ~0.67 s at 50 MHz
  logic [20:0] act = '0;
  always_ff @(posedge clk) begin
    hb <= hb + 1'b1;
    if (bus_req) act <= '1; else if (act != 0) act <= act - 1'b1;
  end

  assign led = {2'b00, act != 0, stop_available[0], done[0], busy[0], rst_n, hb[25]};
endmodule
