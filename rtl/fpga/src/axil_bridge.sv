// AXI4-Lite slave (32-bit data, 16-bit address window) <-> register bus.
// One transaction at a time (a fully latched write goes first).  WSTRB is ignored:
// every write is a full 32-bit register write.  If the bus does not acknowledge within
// ACK_TIMEOUT clocks the transaction completes with SLVERR (reads return 0).
module axil_bridge #(
  parameter int ACK_TIMEOUT = 1024
) (
  input  logic        clk,
  input  logic        rst_n,
  // AXI4-Lite slave
  input  logic [15:0] s_axi_awaddr,
  input  logic        s_axi_awvalid,
  output logic        s_axi_awready,
  input  logic [31:0] s_axi_wdata,
  input  logic [3:0]  s_axi_wstrb,
  input  logic        s_axi_wvalid,
  output logic        s_axi_wready,
  output logic [1:0]  s_axi_bresp,
  output logic        s_axi_bvalid,
  input  logic        s_axi_bready,
  input  logic [15:0] s_axi_araddr,
  input  logic        s_axi_arvalid,
  output logic        s_axi_arready,
  output logic [31:0] s_axi_rdata,
  output logic [1:0]  s_axi_rresp,
  output logic        s_axi_rvalid,
  input  logic        s_axi_rready,
  // register bus
  output logic        bus_req,
  output logic        bus_we,
  output logic [15:0] bus_addr,
  output logic [31:0] bus_wdata,
  input  logic        bus_ack,
  input  logic [31:0] bus_rdata
);
  typedef enum logic [2:0] {S_IDLE, S_WAIT, S_BRESP, S_RRESP} state_t;
  state_t state;

  logic        aw_got, w_got;
  logic [15:0] aw_q;
  logic [31:0] w_q;
  logic [$clog2(ACK_TIMEOUT+1)-1:0] tmo;

  // address/data channels are accepted independently while no write is latched or in flight
  assign s_axi_awready = !aw_got && state == S_IDLE;
  assign s_axi_wready  = !w_got  && state == S_IDLE;
  assign s_axi_arready = state == S_IDLE && !(aw_got && w_got);

  always_ff @(posedge clk) begin
    bus_req <= 1'b0;
    if (!rst_n) begin
      state <= S_IDLE; aw_got <= 1'b0; w_got <= 1'b0;
      s_axi_bvalid <= 1'b0; s_axi_rvalid <= 1'b0; s_axi_bresp <= 2'b00; s_axi_rresp <= 2'b00;
      s_axi_rdata <= '0; bus_we <= 1'b0; bus_addr <= '0; bus_wdata <= '0; tmo <= '0;
    end else begin
      case (state)
        S_IDLE: begin
          if (s_axi_awvalid && s_axi_awready) begin aw_got <= 1'b1; aw_q <= s_axi_awaddr; end
          if (s_axi_wvalid  && s_axi_wready)  begin w_got  <= 1'b1; w_q  <= s_axi_wdata;  end
          if (aw_got && w_got) begin                      // launch write
            bus_req <= 1'b1; bus_we <= 1'b1; bus_addr <= {aw_q[15:2], 2'b00}; bus_wdata <= w_q;
            aw_got <= 1'b0; w_got <= 1'b0; tmo <= '0; state <= S_WAIT;
          end else if (s_axi_arvalid && s_axi_arready) begin   // launch read
            bus_req <= 1'b1; bus_we <= 1'b0; bus_addr <= {s_axi_araddr[15:2], 2'b00};
            tmo <= '0; state <= S_WAIT;
          end
        end
        S_WAIT: begin
          tmo <= tmo + 1'b1;
          if (bus_ack) begin
            if (bus_we) begin s_axi_bvalid <= 1'b1; s_axi_bresp <= 2'b00; state <= S_BRESP; end
            else begin s_axi_rvalid <= 1'b1; s_axi_rresp <= 2'b00; s_axi_rdata <= bus_rdata; state <= S_RRESP; end
          end else if (32'(tmo) == ACK_TIMEOUT - 1) begin
            if (bus_we) begin s_axi_bvalid <= 1'b1; s_axi_bresp <= 2'b10; state <= S_BRESP; end
            else begin s_axi_rvalid <= 1'b1; s_axi_rresp <= 2'b10; s_axi_rdata <= '0; state <= S_RRESP; end
          end
        end
        S_BRESP: if (s_axi_bready) begin s_axi_bvalid <= 1'b0; state <= S_IDLE; end
        S_RRESP: if (s_axi_rready) begin s_axi_rvalid <= 1'b0; state <= S_IDLE; end
        default: state <= S_IDLE;
      endcase
    end
  end
endmodule
