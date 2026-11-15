# Nexys Video non-project build.  Usage (from any directory):
#   vivado -mode batch -source build.tcl -tclargs [out_dir] [n_engines]
# out_dir defaults to $env(LOCALAPPDATA)/christopher/vivado/nexys_video (never inside the project/Dropbox).
set here [file dirname [file normalize [info script]]]
set root [file normalize [file join $here .. .. .. ..]]

if {[llength $argv] > 0} {
    set out [file normalize [lindex $argv 0]]
} elseif {[info exists ::env(LOCALAPPDATA)]} {
    set out [file join $::env(LOCALAPPDATA) christopher vivado nexys_video]
} else {
    set out [file join $::env(HOME) .christopher vivado nexys_video]
}
set n_engines [expr {[llength $argv] > 1 ? [lindex $argv 1] : 1}]
file mkdir $out
puts "output directory: $out   engines: $n_engines"

set part xc7a200tsbg484-1
set_part $part

read_verilog -sv [file join $root rtl src wirings_pkg.sv]
foreach f [lsort [glob [file join $root rtl fpga src *.sv]]] { read_verilog -sv $f }
read_verilog -sv [file join $here top.sv]
read_xdc [file join $here nexys_video.xdc]

synth_design -top top -part $part -generic N_ENGINES=$n_engines -flatten_hierarchy rebuilt
write_checkpoint -force [file join $out post_synth.dcp]
report_utilization    -file [file join $out utilization_synth.rpt]

opt_design
place_design
phys_opt_design
write_checkpoint -force [file join $out post_place.dcp]
route_design
write_checkpoint -force [file join $out post_route.dcp]

report_utilization       -file [file join $out utilization.rpt]
report_timing_summary    -file [file join $out timing_summary.rpt] -max_paths 10
report_clock_utilization -file [file join $out clock_utilization.rpt]
report_drc               -file [file join $out drc.rpt]
report_power             -file [file join $out power.rpt]

set wns [get_property SLACK [get_timing_paths -max_paths 1 -setup]]
puts "post-route worst setup slack: $wns ns"
write_bitstream -force [file join $out top.bit]
puts "bitstream: [file join $out top.bit]"
