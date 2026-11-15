## Nexys Video constraints.  Pin locations / IO standards copied from Digilent's official master XDC:
##   https://github.com/Digilent/digilent-xdc/blob/master/Nexys-Video-Master.xdc
## Port names are mapped to our top.sv (sys_clk, cpu_resetn, uart_*, led[]).

## 100 MHz system clock (sysclk)
set_property -dict { PACKAGE_PIN R4    IOSTANDARD LVCMOS33 } [get_ports { sys_clk }]
create_clock -add -name sys_clk_pin -period 10.00 -waveform {0 5} [get_ports sys_clk]

## CPU reset button (active low)
set_property -dict { PACKAGE_PIN G4    IOSTANDARD LVCMOS15 } [get_ports { cpu_resetn }]

## USB-UART (names are from the FTDI chip's point of view, as in the Digilent XDC)
##   uart_tx_in  = FTDI TX  -> FPGA input  (our rxd)
##   uart_rx_out = FTDI RX  <- FPGA output (our txd)
set_property -dict { PACKAGE_PIN AA19  IOSTANDARD LVCMOS33 } [get_ports { uart_rx_out }]
set_property -dict { PACKAGE_PIN V18   IOSTANDARD LVCMOS33 } [get_ports { uart_tx_in }]

## LEDs
set_property -dict { PACKAGE_PIN T14   IOSTANDARD LVCMOS25 } [get_ports { led[0] }]
set_property -dict { PACKAGE_PIN T15   IOSTANDARD LVCMOS25 } [get_ports { led[1] }]
set_property -dict { PACKAGE_PIN T16   IOSTANDARD LVCMOS25 } [get_ports { led[2] }]
set_property -dict { PACKAGE_PIN U16   IOSTANDARD LVCMOS25 } [get_ports { led[3] }]
set_property -dict { PACKAGE_PIN V15   IOSTANDARD LVCMOS25 } [get_ports { led[4] }]
set_property -dict { PACKAGE_PIN W16   IOSTANDARD LVCMOS25 } [get_ports { led[5] }]
set_property -dict { PACKAGE_PIN W15   IOSTANDARD LVCMOS25 } [get_ports { led[6] }]
set_property -dict { PACKAGE_PIN Y13   IOSTANDARD LVCMOS25 } [get_ports { led[7] }]

## Asynchronous / slow I/O: not timed
set_false_path -from [get_ports { uart_tx_in cpu_resetn }]
set_false_path -to   [get_ports { uart_rx_out led[*] }]

set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
