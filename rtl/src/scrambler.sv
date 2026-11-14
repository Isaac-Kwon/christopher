// One drum set (scrambler): the Enigma rotor/reflector stack without plugboard, as a 26-entry
// permutation for the given wheel order and core offsets. Purely combinational.
//
// Signal path (same as christopher_core::bombe::WheelOrder::scrambler):
//   right fwd -> middle fwd -> left fwd -> [greek fwd] -> reflector
//   -> [greek bwd] -> left bwd -> middle bwd -> right bwd
// The result is an involution (reflector), so the same table serves both directions.

module scrambler
(
  input  logic [1:0]       refl_id,
  input  logic             greek_en,
  input  logic [3:0]       greek_id,
  input  logic [3:0]       left_id,
  input  logic [3:0]       middle_id,
  input  logic [3:0]       right_id,
  input  logic [4:0]       core_g,
  input  logic [4:0]       core_l,
  input  logic [4:0]       core_m,
  input  logic [4:0]       core_r,
  output logic [25:0][4:0] perm
);

  logic [4:0] x;
  logic [4:0] letter;

  always_comb begin
    for (int c = 0; c < 26; c++) begin
      letter = c[4:0];
      x = wirings_pkg::rotor_fwd(right_id, core_r, letter);
      x = wirings_pkg::rotor_fwd(middle_id, core_m, x);
      x = wirings_pkg::rotor_fwd(left_id, core_l, x);
      if (greek_en) x = wirings_pkg::rotor_fwd(greek_id, core_g, x);
      x = wirings_pkg::reflect(refl_id, x);
      if (greek_en) x = wirings_pkg::rotor_bwd(greek_id, core_g, x);
      x = wirings_pkg::rotor_bwd(left_id, core_l, x);
      x = wirings_pkg::rotor_bwd(middle_id, core_m, x);
      x = wirings_pkg::rotor_bwd(right_id, core_r, x);
      perm[c] = x;
    end
  end

endmodule
