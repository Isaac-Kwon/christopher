// UART (8N1) <-> register bus bridge.  Protocol: see rtl/fpga/README.md ("UART protocol").
//   'W' addr[7:0] addr[15:8] d[7:0] d[15:8] d[23:16] d[31:24]  -> 0x06
//   'R' addr[7:0] addr[15:8]                                   -> d[7:0] .. d[31:24]
// Any other byte received while idle is ignored.  A partial frame is abandoned when no byte arrives
// for TIMEOUT_CYCLES clocks (default 10 ms), which resynchronises the stream.  Bytes that arrive
// while a command is executing / being answered are dropped (the host is lock-step).
module uart_bridge #(
  parameter int CLK_HZ         = 100_000_000,
  parameter int BAUD           = 921_600,
  parameter int TIMEOUT_CYCLES = CLK_HZ / 100,
  parameter int ACK_TIMEOUT    = 256            // bus clocks to wait for bus_ack before answering 0
) (
  input  logic        clk,
  input  logic        rst_n,
  input  logic        rxd,
  output logic        txd,
  output logic        bus_req,
  output logic        bus_we,
  output logic [15:0] bus_addr,
  output logic [31:0] bus_wdata,
  input  logic        bus_ack,
  input  logic [31:0] bus_rdata
);
  localparam int DIV = (CLK_HZ + BAUD / 2) / BAUD;        // clocks per bit (rounded)
  localparam int DW  = $clog2(DIV + 1);
  localparam int TW  = $clog2(TIMEOUT_CYCLES + 1);

  // ---------------- receiver ----------------
  logic rx_s1, rx_s2;
  always_ff @(posedge clk) begin
    if (!rst_n) {rx_s2, rx_s1} <= 2'b11;
    else        {rx_s2, rx_s1} <= {rx_s1, rxd};
  end

  typedef enum logic [1:0] {R_IDLE, R_START, R_DATA, R_STOP} rx_state_t;
  rx_state_t     rx_state;
  logic [DW-1:0] rx_cnt;
  logic [2:0]    rx_bit;
  logic [7:0]    rx_shift;
  logic          rx_valid;
  logic [7:0]    rx_byte;

  always_ff @(posedge clk) begin
    rx_valid <= 1'b0;
    if (!rst_n) begin
      rx_state <= R_IDLE;
      rx_cnt   <= '0;
      rx_bit   <= '0;
    end else begin
      case (rx_state)
        R_IDLE:  if (!rx_s2) begin rx_state <= R_START; rx_cnt <= DW'(DIV / 2 - 1); end
        R_START: if (rx_cnt == 0) begin           // middle of start bit
                   if (!rx_s2) begin rx_state <= R_DATA; rx_cnt <= DW'(DIV - 1); rx_bit <= '0; end
                   else rx_state <= R_IDLE;       // glitch
                 end else rx_cnt <= rx_cnt - 1'b1;
        R_DATA:  if (rx_cnt == 0) begin
                   rx_shift <= {rx_s2, rx_shift[7:1]};
                   rx_cnt   <= DW'(DIV - 1);
                   if (rx_bit == 3'd7) rx_state <= R_STOP;
                   rx_bit <= rx_bit + 1'b1;
                 end else rx_cnt <= rx_cnt - 1'b1;
        R_STOP:  if (rx_cnt == 0) begin
                   rx_state <= R_IDLE;
                   if (rx_s2) begin rx_valid <= 1'b1; rx_byte <= rx_shift; end  // framing error -> drop
                 end else rx_cnt <= rx_cnt - 1'b1;
        default: rx_state <= R_IDLE;
      endcase
    end
  end

  // ---------------- transmitter ----------------
  logic          tx_start;
  logic [7:0]    tx_data;
  logic          tx_busy;
  logic [9:0]    tx_shift;
  logic [3:0]    tx_nbits;
  logic [DW-1:0] tx_cnt;

  assign txd = tx_shift[0];
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      tx_shift <= 10'h3FF;
      tx_busy  <= 1'b0;
      tx_nbits <= '0;
      tx_cnt   <= '0;
    end else if (!tx_busy) begin
      if (tx_start) begin
        tx_shift <= {1'b1, tx_data, 1'b0};
        tx_busy  <= 1'b1;
        tx_nbits <= 4'd10;
        tx_cnt   <= DW'(DIV - 1);
      end
    end else if (tx_cnt == 0) begin
      tx_shift <= {1'b1, tx_shift[9:1]};
      tx_cnt   <= DW'(DIV - 1);
      tx_nbits <= tx_nbits - 1'b1;
      if (tx_nbits == 4'd1) tx_busy <= 1'b0;
    end else tx_cnt <= tx_cnt - 1'b1;
  end

  // ---------------- frame state machine ----------------
  typedef enum logic [2:0] {F_IDLE, F_ADDR, F_DATA, F_EXEC, F_SEND} f_state_t;
  f_state_t      fs;
  logic          is_write;
  logic [2:0]    nbytes;       // bytes collected within ADDR / DATA phase
  logic [TW-1:0] to_cnt;
  logic [7:0]    ack_cnt;
  logic [31:0]   resp;
  logic [2:0]    resp_left;    // response bytes still to send
  logic          resp_sent;    // tx_start issued for current byte, waiting for it to finish

  always_ff @(posedge clk) begin
    bus_req  <= 1'b0;
    tx_start <= 1'b0;
    if (!rst_n) begin
      fs <= F_IDLE; nbytes <= '0; to_cnt <= '0; is_write <= 1'b0;
      bus_we <= 1'b0; bus_addr <= '0; bus_wdata <= '0; resp <= '0; resp_left <= '0;
      resp_sent <= 1'b0; ack_cnt <= '0;
    end else begin
      case (fs)
        F_IDLE: if (rx_valid) begin
          nbytes <= '0;
          to_cnt <= TW'(TIMEOUT_CYCLES);
          if (rx_byte == 8'h57)      begin fs <= F_ADDR; is_write <= 1'b1; end
          else if (rx_byte == 8'h52) begin fs <= F_ADDR; is_write <= 1'b0; end
        end
        F_ADDR, F_DATA: begin
          if (rx_valid) begin
            to_cnt <= TW'(TIMEOUT_CYCLES);
            nbytes <= nbytes + 1'b1;
            if (fs == F_ADDR) begin
              if (nbytes == 0) bus_addr[7:0]  <= rx_byte;
              else             bus_addr[15:8] <= rx_byte;
              if (nbytes == 1) begin
                nbytes <= '0;
                if (is_write) fs <= F_DATA;
                else begin fs <= F_EXEC; bus_we <= 1'b0; bus_req <= 1'b1; ack_cnt <= '0; end
              end
            end else begin
              case (nbytes[1:0])
                2'd0: bus_wdata[7:0]   <= rx_byte;
                2'd1: bus_wdata[15:8]  <= rx_byte;
                2'd2: bus_wdata[23:16] <= rx_byte;
                2'd3: bus_wdata[31:24] <= rx_byte;
              endcase
              if (nbytes == 3) begin
                fs <= F_EXEC; bus_we <= 1'b1; bus_req <= 1'b1; ack_cnt <= '0;
              end
            end
          end else if (to_cnt == 0) fs <= F_IDLE;      // partial frame timed out
          else to_cnt <= to_cnt - 1'b1;
        end
        F_EXEC: begin
          ack_cnt <= ack_cnt + 1'b1;
          if (bus_ack || ack_cnt == 8'(ACK_TIMEOUT - 1)) begin
            if (is_write) begin resp <= 32'h06; resp_left <= 3'd1; end
            else begin resp <= bus_ack ? bus_rdata : 32'h0; resp_left <= 3'd4; end
            fs <= F_SEND; resp_sent <= 1'b0;
          end
        end
        F_SEND: begin
          if (!resp_sent) begin
            if (!tx_busy && !tx_start) begin
              tx_data <= resp[7:0]; tx_start <= 1'b1; resp_sent <= 1'b1;
              resp <= {8'h00, resp[31:8]};
              resp_left <= resp_left - 1'b1;
            end
          end else if (tx_busy) begin
            // byte accepted; wait for it to finish
          end else if (!tx_start) begin
            resp_sent <= 1'b0;
            if (resp_left == 0) fs <= F_IDLE;
          end
        end
        default: fs <= F_IDLE;
      endcase
    end
  end
endmodule
