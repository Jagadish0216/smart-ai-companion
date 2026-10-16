include <dimensions.scad>
use <utilities.scad>
use <components.scad>
use <head_shell.scad>
use <head_rear_cover.scad>
use <front_bezel.scad>
use <camera_insert.scad>
use <base_shell.scad>
use <base_bottom_cover.scad>
use <speaker_mount.scad>
use <head_adapters.scad>
MODE = is_undef(MODE) ? "assembly" : MODE;
PART = is_undef(PART) ? "all" : PART;
SHOW_COMPONENTS = is_undef(SHOW_COMPONENTS) ? true : SHOW_COMPONENTS;
CAMERA_INSTALLED = is_undef(CAMERA_INSTALLED) ? true : CAMERA_INSTALLED;
MESH_PREVIEW = is_undef(MESH_PREVIEW) ? false : MESH_PREVIEW;
// Documentation can use the verified STL meshes to avoid OpenCSG coplanar artifacts.
module head_model() { if(MESH_PREVIEW) import("output/head_shell.stl"); else head_shell(); }
// Finish only: black paint on the existing front face, no new part or geometry.
module head_finished_model() {
    color(col_shell) render(convexity=20) difference() { head_model(); translate([-100,-100,-eps]) cube([200,220,0.3+eps]); }
    color(col_dark) render(convexity=20) intersection() { head_model(); translate([-100,-100,-eps]) cube([200,220,0.3+eps]); }
}
module rear_model() { if(MESH_PREVIEW) translate([0,0,head_d]) rotate([180,0,0]) import("output/head_rear_cover.stl"); else head_rear_cover(); }
module bezel_model() { translate([0,0,bezel_seated_z]) if(MESH_PREVIEW) import("output/front_bezel.stl"); else front_bezel(); }
module camera_model() { if(MESH_PREVIEW) translate([0,camera_y,floor_t-camera_insert_t]) import(CAMERA_INSTALLED ? "output/camera_lens_insert.stl" : "output/camera_blank_insert.stl"); else camera_insert(CAMERA_INSTALLED); }
module carrier_model() { translate([0,camera_y+camera_carrier_y,camera_bracket_z+camera_bracket_t]) if(MESH_PREVIEW) import("output/camera_carrier.stl"); else camera_carrier(); }
module base_model() { if(MESH_PREVIEW) translate([0,0,base_h]) rotate([180,0,0]) import("output/base_shell.stl"); else base_shell(); }
module bottom_model() { if(MESH_PREVIEW) import("output/base_bottom_cover.stl"); else base_bottom_cover(); }
module speaker_model() { if(MESH_PREVIEW) translate([speaker_xy[0],speaker_xy[1],speaker_mount_z]) import("output/universal_speaker_mount.stl"); else speaker_mount(); }
module shell_parts() {
    color(col_shell) base_model();
    color(col_shell) bottom_model();
    color([0.28,0.3,0.33]) speaker_model();
    assembly_head_frame() {
        head_finished_model();
        color(col_dark) button_actuator();
        color(col_dark) button_holder();
        color(col_dark) microphone_holder();
        color(col_shell) rear_model();
        color(col_dark) bezel_model();
        color(CAMERA_INSTALLED ? col_dark : col_shell) camera_model();
        if(CAMERA_INSTALLED) color(col_dark) carrier_model();
    }
}
module all_references() { assembly_head_frame() head_references(); base_references(); }
module assembly() {
    shell_parts();
    feet_reference();
    if(SHOW_COMPONENTS) all_references();
    color([0.1,0.85,0.94]) translate([0,-base_depth_at(led_z)/2-0.2,led_z]) cyl_y(5,2);
    // Artwork is documentation only, on the real LCD active plane.
    assembly_head_frame() color([0.1,0.85,0.94]) for(x=[-14,14]) translate([x+tft_active_x,tft_y-3,tft_front_z-2*eps]) rr(9,19,4,eps);
}
module exploded() {
    color(col_shell) base_model();
    translate([0,0,-preview_explode]) { color(col_shell) bottom_model(); color([0.28,0.3,0.33]) speaker_model(); base_references(); }
    translate([0,0,preview_explode]) assembly_head_frame() {
        color(col_shell) head_model();
        translate([0,0,-preview_explode]) color(col_dark) bezel_model();
        translate([0,0,-preview_explode/2]) tft_reference();
        translate([0,0,preview_explode]) { color(col_shell) rear_model(); esp_reference(); }
        translate([0,0,-preview_explode/2]) color(col_dark) camera_model();
        translate([0,-15,0]) mic_reference();
        camera_reference(); button_reference();
    }
}
module cutaway() {
    color(col_shell) bottom_model();
    color([0.28,0.3,0.33]) speaker_model();
    color(col_shell) render(convexity=20) difference() {
        base_model();
        translate([15,-base_d,0]) cube([base_w,2*base_d,base_h+eps]);
        translate([-base_w,-base_d,-eps]) cube([2*base_w,base_d+20,base_h+2*eps]);
    }
    color(col_shell) render(convexity=20) difference() {
        assembly_head_frame() head_model();
        assembly_head_frame() translate([0,-head_h,-eps]) cube([head_w,2*head_h,2*head_d]);
    }
    color(col_dark) render(convexity=20) difference() {
        assembly_head_frame() { bezel_model(); camera_model(); }
        assembly_head_frame() translate([0,-head_h,-eps]) cube([head_w,2*head_h,2*head_d]);
    }
    all_references(); cooler_air_reference(); cable_reference(); feet_reference();
    assembly_head_frame() { color(col_dark) button_actuator(); color(col_dark) microphone_holder(); color(col_dark) button_holder(); }
}
module head_service() {
    color(col_shell) head_model();
    tft_reference(); mic_reference(); camera_reference(); button_reference(); color(col_dark) carrier_model();
    translate([head_w+15,0,head_d]) rotate([180,0,0]) { color(col_shell) rear_model(); esp_reference(); }
}
module base_service() { color(col_shell) bottom_model(); color([0.28,0.3,0.33]) speaker_model(); base_references(); plugs_reference(); }
module print_layout() {
    color(col_shell) {
        head_model();
        translate([layout_pitch_x,0,head_d]) rotate([180,0,0]) rear_model();
        translate([0,layout_pitch_y,base_h]) rotate([180,0,0]) base_model();
        translate([layout_pitch_x,layout_pitch_y,0]) bottom_model();
    }
    color(col_dark) {
        translate([-layout_pitch_x,0,-bezel_seated_z]) bezel_model();
        translate([-layout_pitch_x-speaker_xy[0],layout_pitch_y-speaker_xy[1],-speaker_mount_z]) speaker_model();
        translate([-layout_pitch_x-30,layout_pitch_y-55,0]) if(MESH_PREVIEW) import("output/camera_blank_insert.stl"); else camera_insert_print(false);
        translate([-layout_pitch_x+30,layout_pitch_y-55,0]) if(MESH_PREVIEW) import("output/camera_lens_insert.stl"); else camera_insert_print(true);
        translate([-layout_pitch_x,layout_pitch_y+65,0]) microphone_holder_print();
        translate([-layout_pitch_x,layout_pitch_y-85,0]) if(MESH_PREVIEW) import("output/camera_carrier.stl"); else camera_carrier();
    }
}
// Part selection bypasses every reference model, foot and preview annotation.
if(PART == "head_shell") head_shell();
else if(PART == "head_rear_cover") head_rear_cover_print();
else if(PART == "front_bezel") front_bezel();
else if(PART == "base_shell") base_shell_print();
else if(PART == "base_bottom_cover") base_bottom_cover();
else if(PART == "camera_blank") camera_insert_print(false);
else if(PART == "camera_lens") camera_insert_print(true);
else if(PART == "speaker_mount") speaker_mount_print();
else if(PART == "button_actuator") button_actuator_print();
else if(PART == "camera_carrier") camera_carrier();
else if(MODE == "assembly") assembly();
else if(MODE == "exploded") exploded();
else if(MODE == "cutaway") cutaway();
else if(MODE == "components") { all_references(); plugs_reference(); cooler_air_reference(); cable_reference(); }
else if(MODE == "print_layout") print_layout();
else if(MODE == "head_service") head_service();
else if(MODE == "base_service") base_service();
else assert(false,"Unknown MODE / PART");
