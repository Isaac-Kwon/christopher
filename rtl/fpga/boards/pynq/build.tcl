# PYNQ-Z1/Z2 block design + bitstream + .hwh.
#   vivado -mode batch -source build.tcl -tclargs [out_dir] [n_engines] [stage]
#     out_dir     default $env(LOCALAPPDATA)/christopher/vivado/pynq   (never inside the project / Dropbox)
#     n_engines   default 1
#     stage       "bd" = stop after creating and validating the block design (quick check),
#                 "full" (default) = synthesis, implementation, bitstream, export
# Output: <out_dir>/bombe.bit and <out_dir>/bombe.hwh (same stem: copy both to the board and
#   load with  pynq.Overlay("bombe.bit") ; then bombe_fpga.MmioTransport(base=ip_dict["bombe"]["phys_addr"])).
#
# PS7 settings: the stock PYNQ-Z2 board file (tul.com.tw:pynq-z2) is used when installed; otherwise the
# PS7 is configured by hand below (FCLK0 = 100 MHz, M_AXI_GP0 on).  The PS peripherals/DDR are
# irrelevant for a PYNQ overlay: Linux is already running and only the PL bitstream + clocks are loaded.
# No PL pin constraints are needed (all signals are internal to the PL / PS).

set here [file dirname [file normalize [info script]]]
set root [file normalize [file join $here .. .. .. ..]]

if {[llength $argv] > 0} {
    set out [file normalize [lindex $argv 0]]
} elseif {[info exists ::env(LOCALAPPDATA)]} {
    set out [file join $::env(LOCALAPPDATA) christopher vivado pynq]
} else {
    set out [file join $::env(HOME) .christopher vivado pynq]
}
set n_engines [expr {[llength $argv] > 1 ? [lindex $argv 1] : 1}]
set stage     [expr {[llength $argv] > 2 ? [lindex $argv 2] : "full"}]
file mkdir $out
puts "output directory: $out   engines: $n_engines   stage: $stage"

set part xc7z020clg400-1
set proj [file join $out proj]
create_project -force bombe_pynq $proj -part $part

# optional board files (TUL PYNQ-Z2: https://github.com/Xilinx/XilinxBoardStore or tul.com.tw)
set board_used 0
foreach bp [get_board_parts -quiet -filter {NAME =~ "*pynq-z2*"}] {
    set_property board_part $bp [current_project]
    set board_used 1
    puts "using board part $bp"
    break
}

add_files -norecurse [file join $root rtl src wirings_pkg.sv]
foreach f [lsort [glob [file join $root rtl fpga src *.sv]]] { add_files -norecurse $f }
add_files -norecurse [file join $here pynq_top.sv]
set_property file_type SystemVerilog [get_files *.sv]
update_compile_order -fileset sources_1

create_bd_design bombe_bd

# ---- Zynq PS ----
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:processing_system7 ps7]
if {$board_used} {
    apply_bd_automation -rule xilinx.com:bd_rule:processing_system7 -config {make_external "FIXED_IO, DDR" apply_board_preset "1" Master "Disable" Slave "Disable"} $ps
}
set_property -dict [list \
    CONFIG.PCW_USE_M_AXI_GP0 1 \
    CONFIG.PCW_FPGA0_PERIPHERAL_FREQMHZ 100 \
    CONFIG.PCW_EN_CLK0_PORT 1 \
    CONFIG.PCW_EN_RST0_PORT 1 \
    CONFIG.PCW_USE_FABRIC_INTERRUPT 0 \
] $ps
if {!$board_used} {
    # PYNQ-Z2 memory/peripheral settings (from the TUL board preset); needed only for a complete PS7 definition.
    set_property -dict [list \
        CONFIG.PCW_UIPARAM_DDR_PARTNO {MT41K256M16 RE-125} \
        CONFIG.PCW_UIPARAM_DDR_BUS_WIDTH {32 Bit} \
        CONFIG.PCW_UIPARAM_DDR_MEMORY_TYPE {DDR 3} \
        CONFIG.PCW_PRESET_BANK1_VOLTAGE {LVCMOS 1.8V} \
        CONFIG.PCW_QSPI_PERIPHERAL_ENABLE 1 \
        CONFIG.PCW_QSPI_GRP_SINGLE_SS_ENABLE 1 \
        CONFIG.PCW_SD0_PERIPHERAL_ENABLE 1 \
        CONFIG.PCW_UART0_PERIPHERAL_ENABLE 1 \
        CONFIG.PCW_UART0_UART0_IO {MIO 14 .. 15} \
        CONFIG.PCW_ENET0_PERIPHERAL_ENABLE 1 \
        CONFIG.PCW_ENET0_ENET0_IO {MIO 16 .. 27} \
        CONFIG.PCW_USB0_PERIPHERAL_ENABLE 1 \
    ] $ps
    make_bd_intf_pins_external [get_bd_intf_pins ps7/DDR] [get_bd_intf_pins ps7/FIXED_IO]
}

# ---- reset, interconnect, our module ----
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset rst_ps7_100M]
set ic  [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_interconnect axi_ic]
set_property -dict [list CONFIG.NUM_MI 1 CONFIG.NUM_SI 1] $ic

set bombe [create_bd_cell -type module -reference pynq_top bombe]
set_property -dict [list CONFIG.N_ENGINES $n_engines CONFIG.CLK_MHZ 100] $bombe

connect_bd_net [get_bd_pins ps7/FCLK_CLK0] [get_bd_pins ps7/M_AXI_GP0_ACLK] \
    [get_bd_pins rst_ps7_100M/slowest_sync_clk] [get_bd_pins axi_ic/ACLK] \
    [get_bd_pins axi_ic/S00_ACLK] [get_bd_pins axi_ic/M00_ACLK] [get_bd_pins bombe/aclk]
connect_bd_net [get_bd_pins ps7/FCLK_RESET0_N] [get_bd_pins rst_ps7_100M/ext_reset_in]
connect_bd_net [get_bd_pins rst_ps7_100M/interconnect_aresetn] [get_bd_pins axi_ic/ARESETN]
connect_bd_net [get_bd_pins rst_ps7_100M/peripheral_aresetn] \
    [get_bd_pins axi_ic/S00_ARESETN] [get_bd_pins axi_ic/M00_ARESETN] [get_bd_pins bombe/aresetn]
connect_bd_intf_net [get_bd_intf_pins ps7/M_AXI_GP0] [get_bd_intf_pins axi_ic/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_ic/M00_AXI] [get_bd_intf_pins bombe/S_AXI]

# 64 KiB register window at 0x4000_0000 (start of the PS M_AXI_GP0 range)
assign_bd_address
set seg [get_bd_addr_segs ps7/Data/SEG_bombe_Reg]
set_property offset 0x40000000 $seg
set_property range 64K $seg
validate_bd_design
save_bd_design
puts "address map:"
foreach s [get_bd_addr_segs -of_objects [get_bd_addr_spaces ps7/Data]] {
    puts "  $s  offset [get_property OFFSET $s]  range [get_property RANGE $s]"
}
if {$stage eq "bd"} {
    puts "block design validated (stage bd): done"
    close_project
    exit 0
}

# ---- build ----
make_wrapper -files [get_files bombe_bd.bd] -top
add_files -norecurse [file join $proj bombe_pynq.gen sources_1 bd bombe_bd hdl bombe_bd_wrapper.v]
set_property top bombe_bd_wrapper [current_fileset]
update_compile_order -fileset sources_1

launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {
    error "implementation failed: see $proj/bombe_pynq.runs/impl_1/runme.log"
}
open_run impl_1
report_utilization    -file [file join $out utilization.rpt]
report_timing_summary -file [file join $out timing_summary.rpt] -max_paths 10
puts "post-route worst setup slack: [get_property SLACK [get_timing_paths -max_paths 1 -setup]] ns"

# PYNQ wants bombe.bit and bombe.hwh with the same stem
file copy -force [file join $proj bombe_pynq.runs impl_1 bombe_bd_wrapper.bit] [file join $out bombe.bit]
set hwh [glob -nocomplain [file join $proj bombe_pynq.gen sources_1 bd bombe_bd hw_handoff *.hwh]]
if {[llength $hwh] == 0} { set hwh [glob -nocomplain [file join $proj bombe_pynq.srcs sources_1 bd bombe_bd hw_handoff *.hwh]] }
file copy -force [lindex $hwh 0] [file join $out bombe.hwh]
puts "exported: [file join $out bombe.bit]  [file join $out bombe.hwh]"
