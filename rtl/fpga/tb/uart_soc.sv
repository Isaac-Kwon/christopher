// Test-only top: uart_bridge + bombe_array (what boards/nexys_video/top.sv wires, minus pads).
module uart_soc #(parameter int CLK_HZ = 1_843_200, parameter int BAUD = 115_200, parameter int N_ENGINES = 2) (
  input  logic clk, rst_n, rxd,
  output logic txd,
  output logic [N_ENGINES-1:0] busy, done, stop_available
);
  logic        bus_req, bus_we, bus_ack;
  logic [15:0] bus_addr;
  logic [31:0] bus_wdata, bus_rdata;
  uart_bridge #(.CLK_HZ(CLK_HZ), .BAUD(BAUD), .TIMEOUT_CYCLES(2000)) u_uart (
    .clk, .rst_n, .rxd, .txd, .bus_req, .bus_we, .bus_addr, .bus_wdata, .bus_ack, .bus_rdata);
  bombe_array #(.N_ENGINES(N_ENGINES)) u_array (
    .clk, .rst_n, .bus_req, .bus_we, .bus_addr, .bus_wdata, .bus_ack, .bus_rdata,
    .busy, .done, .stop_available);
endmodule
