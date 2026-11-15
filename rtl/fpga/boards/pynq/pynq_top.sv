// Module-reference wrapper for the Vivado block design: AXI4-Lite slave -> bombe_array.
// X_INTERFACE attributes let IP Integrator recognise the AXI4-Lite interface and clock/reset.
module pynq_top #(
  parameter int N_ENGINES = 1,
  parameter int MAX_EDGES = 16,
  parameter int FIFO_LOG2 = 6,
  parameter int CLK_MHZ   = 100
) (
  (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
  (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET aresetn, FREQ_HZ 100000000" *)
  input  logic        aclk,
  (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
  (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
  input  logic        aresetn,

  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
  input  logic [15:0] s_axi_awaddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *)
  input  logic [2:0]  s_axi_awprot,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
  input  logic        s_axi_awvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
  output logic        s_axi_awready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
  input  logic [31:0] s_axi_wdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
  input  logic [3:0]  s_axi_wstrb,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
  input  logic        s_axi_wvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
  output logic        s_axi_wready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
  output logic [1:0]  s_axi_bresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
  output logic        s_axi_bvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
  input  logic        s_axi_bready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
  input  logic [15:0] s_axi_araddr,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *)
  input  logic [2:0]  s_axi_arprot,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
  input  logic        s_axi_arvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
  output logic        s_axi_arready,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
  output logic [31:0] s_axi_rdata,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
  output logic [1:0]  s_axi_rresp,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
  output logic        s_axi_rvalid,
  (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
  input  logic        s_axi_rready,

  output logic [N_ENGINES-1:0] busy,
  output logic [N_ENGINES-1:0] done,
  output logic [N_ENGINES-1:0] stop_available
);
  logic        bus_req, bus_we, bus_ack;
  logic [15:0] bus_addr;
  logic [31:0] bus_wdata, bus_rdata;

  axil_bridge u_axil (
    .clk(aclk), .rst_n(aresetn),
    .s_axi_awaddr, .s_axi_awvalid, .s_axi_awready, .s_axi_wdata, .s_axi_wstrb, .s_axi_wvalid,
    .s_axi_wready, .s_axi_bresp, .s_axi_bvalid, .s_axi_bready, .s_axi_araddr, .s_axi_arvalid,
    .s_axi_arready, .s_axi_rdata, .s_axi_rresp, .s_axi_rvalid, .s_axi_rready,
    .bus_req, .bus_we, .bus_addr, .bus_wdata, .bus_ack, .bus_rdata
  );

  bombe_array #(.N_ENGINES(N_ENGINES), .MAX_EDGES(MAX_EDGES), .FIFO_LOG2(FIFO_LOG2), .CLK_MHZ(CLK_MHZ)) u_array (
    .clk(aclk), .rst_n(aresetn),
    .bus_req, .bus_we, .bus_addr, .bus_wdata, .bus_ack, .bus_rdata,
    .busy, .done, .stop_available
  );
endmodule
