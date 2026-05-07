// ─────────────────────────────────────────────────────────────────────────
// PBGA 256 — Plastic Ball Grid Array, 1.0 mm pitch, 16×16 grid
//
// Reference geometry:
//   Darveaux R. (2000), "Effect of Rate of Change of Temperature on Solder
//   Joint Fatigue", Proc. ASME InterPACK 2000.
//   Geometry per Amkor PBGA 256, body 27×27 mm, 1.0 mm pitch.
//
// Validation case:  ElectroniX validation/case_01_darveaux2000_pbga256
// Expected Nf:      6,500 cycles (η, β=4.5) for 0/+100°C TC1 profile
//
// Bodies (each is a separate region in the .pcprep, with its own material):
//   1. PCB substrate  — FR4 (id 1)
//   2. PCB pads       — Cu (id 2)
//   3. Solder balls   — SAC305 (id 4) — 256 barrel-shaped joints
//   4. Pkg substrate  — BT laminate (id 9)
//   5. Die attach     — epoxy (id 11)
//   6. Die            — Si (id 10)
//   7. Mold compound  — EMC (id 8)
// ─────────────────────────────────────────────────────────────────────────

// === Render selector =====================================================
// Render the whole assembly, or just one body for separate STL export.
//   "all"        — full assembly (preview)
//   "pcb"        — FR4 substrate only
//   "pads"       — copper pads only
//   "balls"      — 256 SAC305 solder joints only
//   "substrate"  — BT laminate package substrate
//   "die_attach" — die-attach epoxy
//   "die"        — silicon die
//   "mold"       — EMC mold compound
//
// CLI export:
//   openscad -o balls.stl -D 'RENDER="balls"' board.scad
RENDER = "all";

// === Geometry (mm) =======================================================
// Solder joint
ball_pitch       = 1.0;
ball_count_side  = 16;          // 16 × 16 = 256
ball_diameter    = 0.60;        // diameter at the equator (bulge)
solder_height    = 0.50;        // standoff between PCB pad and substrate pad
pad_diameter     = 0.55;        // SMD pad diameter (top of PCB & bottom of substrate)

// Package body (27 × 27 mm typical PBGA 256)
pkg_size         = 27.00;
substrate_thick  = 0.36;        // BT laminate thickness
mold_size        = 25.00;       // mold cap inset 1 mm from body edge
mold_thick       = 1.10;
die_size         = 10.00;
die_thick        = 0.30;
die_attach_thick = 0.05;

// PCB test coupon
pcb_size         = 50.00;       // 50 × 50 mm board
pcb_thick        = 1.60;        // 4-layer FR4
pad_thick        = 0.035;       // 1 oz Cu

// Mesh resolution (raise for smoother spheres → larger STL)
$fn = 24;

// ── Derived ─────────────────────────────────────────────────────────────
// Centre the 16×16 grid on the package
ball_grid_offset = -(ball_count_side - 1) * ball_pitch / 2;

// Stack heights (z-coordinate of each layer's bottom face)
z_pcb_top         = 0;                                 // PCB top surface = z=0
z_pad_top         = pad_thick;                         // top of PCB pad
z_solder_bottom   = z_pad_top;
z_solder_top      = z_solder_bottom + solder_height;
z_substrate_bot   = z_solder_top;
z_substrate_top   = z_substrate_bot + substrate_thick;
z_die_attach_bot  = z_substrate_top;
z_die_attach_top  = z_die_attach_bot + die_attach_thick;
z_die_bot         = z_die_attach_top;
z_die_top         = z_die_bot + die_thick;
z_mold_bot        = z_substrate_top;                   // mold sits on substrate, encapsulates die
z_mold_top        = z_mold_bot + mold_thick;

// === Helpers =============================================================

// Place children at every ball position
module ball_grid() {
    for (i = [0 : ball_count_side - 1])
        for (j = [0 : ball_count_side - 1])
            translate([
                ball_grid_offset + i * ball_pitch,
                ball_grid_offset + j * ball_pitch,
                0
            ])
                children();
}

// One barrel-shaped reflowed solder joint:
// pad-diameter at top & bottom, bulging to ball_diameter at the equator.
// Built with hull() of two thin cylinders (the pads) and a sphere (the bulge).
module solder_joint() {
    hull() {
        // Bottom contact (PCB pad)
        translate([0, 0, z_solder_bottom + 0.005])
            cylinder(h = 0.01, d = pad_diameter, center = true);
        // Top contact (substrate pad)
        translate([0, 0, z_solder_top - 0.005])
            cylinder(h = 0.01, d = pad_diameter, center = true);
        // Equator bulge
        translate([0, 0, (z_solder_bottom + z_solder_top) / 2])
            sphere(d = ball_diameter);
    }
}

// === Bodies ==============================================================

// 1. PCB substrate (FR4) — 50×50×1.6 mm, top surface at z=0
module body_pcb() {
    color("darkgreen")
        translate([0, 0, -pcb_thick/2])
            cube([pcb_size, pcb_size, pcb_thick], center = true);
}

// 2. PCB pads — 1 oz Cu cylinders, one per ball position
module body_pads() {
    color("orange")
        ball_grid()
            translate([0, 0, pad_thick/2])
                cylinder(h = pad_thick, d = pad_diameter, center = true);
}

// 3. Solder balls — 256 SAC305 barrel-shaped joints
module body_solder_balls() {
    color("silver")
        ball_grid()
            solder_joint();
}

// 4. Package substrate (BT laminate)
module body_substrate() {
    color("tan")
        translate([0, 0, z_substrate_bot + substrate_thick/2])
            cube([pkg_size, pkg_size, substrate_thick], center = true);
}

// 5. Die-attach epoxy
module body_die_attach() {
    color("gold")
        translate([0, 0, z_die_attach_bot + die_attach_thick/2])
            cube([die_size, die_size, die_attach_thick], center = true);
}

// 6. Silicon die
module body_die() {
    color("dimgray")
        translate([0, 0, z_die_bot + die_thick/2])
            cube([die_size, die_size, die_thick], center = true);
}

// 7. Mold compound (EMC) — encapsulates the die, sits on the substrate
//    Modelled as a solid block (the die is embedded inside it).
module body_mold() {
    color([0.1, 0.1, 0.1, 0.6])
        translate([0, 0, z_mold_bot + mold_thick/2])
            cube([mold_size, mold_size, mold_thick], center = true);
}

// === Render selector =====================================================
if      (RENDER == "all")        { body_pcb(); body_pads(); body_solder_balls();
                                   body_substrate(); body_die_attach();
                                   body_die(); body_mold(); }
else if (RENDER == "pcb")        body_pcb();
else if (RENDER == "pads")       body_pads();
else if (RENDER == "balls")      body_solder_balls();
else if (RENDER == "substrate")  body_substrate();
else if (RENDER == "die_attach") body_die_attach();
else if (RENDER == "die")        body_die();
else if (RENDER == "mold")       body_mold();
else                             echo("ERROR: unknown RENDER value: ", RENDER);

// ── Reference info (printed to console when rendered) ────────────────────
echo("PBGA 256 — 1.0 mm pitch");
echo("  total balls  =", ball_count_side * ball_count_side);
echo("  package      =", pkg_size, "×", pkg_size, "mm");
echo("  PCB coupon   =", pcb_size, "×", pcb_size, "×", pcb_thick, "mm");
echo("  total height =", z_mold_top, "mm above PCB top surface");
