// Positive-volume intersections must be EMPTY. Intended mating surfaces may touch.
// Uses the actual exported meshes, so run build.py before these checks.
include <dimensions.scad>
use <utilities.scad>
use <components.scad>
use <head_adapters.scad>
CHECK = is_undef(CHECK) ? "head_components" : CHECK;
module hs() { import("output/head_shell.stl"); }
module hc() { translate([0,0,head_d]) rotate([180,0,0]) import("output/head_rear_cover.stl"); }
module bs() { translate([0,0,base_h]) rotate([180,0,0]) import("output/base_shell.stl"); }
module bc() { import("output/base_bottom_cover.stl"); }
module ci() { translate([0,camera_y,floor_t-camera_insert_t]) import("output/camera_lens_insert.stl"); }
module fb() { translate([0,0,bezel_seated_z]) import("output/front_bezel.stl"); }
module sm() { translate([speaker_xy[0],speaker_xy[1],speaker_mount_z]) import("output/universal_speaker_mount.stl"); }
module cc() { translate([0,camera_y+camera_carrier_y,camera_bracket_z+camera_bracket_t+eps]) import("output/camera_carrier.stl"); }
// Small erosion of known plane contacts avoids float precision on binary STL vertices.
// This is not a fit allowance; the component nominal geometry is checked separately.
module head_component_interference() {
    intersection() { hs(); union() {
        // 0.02 mm lift at the intended TFT seating planes eliminates zero-volume contacts.
        translate([0,0,eps]) tft_reference();
        esp_reference(); mic_reference(); camera_reference();
        // Only the internal button body is a keep-out; panel stem/flange are mating hardware.
        translate([head_w/2-wall-button_internal_l+eps,button_y,button_z]) cyl_x(button_body_d,button_internal_l-2*eps);
    } }
}
module base_component_interference() {
    intersection() { bs(); base_references(); }
    intersection() { bc(); translate([0,0,eps]) base_references(); }
}
if(CHECK=="head_components") head_component_interference();
else if(CHECK=="base_components") base_component_interference();
else if(CHECK=="plugs") intersection() { union() { bs(); bc(); } plugs_reference(); }
else if(CHECK=="cooler_air") intersection() {
    union() { bs(); bc(); assembly_head_frame() hs(); }
    intersection() { cooler_air_reference(); translate([-base_w,-base_d,0]) cube([2*base_w,2*base_d,base_h-floor_t-eps]); }
}
else if(CHECK=="head_mates") {
    intersection() { hs(); translate([0,0,eps]) hc(); }
    intersection() { hs(); translate([0,0,-eps]) fb(); }
    intersection() { hs(); translate([0,0,eps]) ci(); }
    intersection() { fb(); ci(); }
}
else if(CHECK=="base_mates") {
    intersection() { bs(); translate([0,0,-eps]) bc(); }
    intersection() { bc(); translate([0,0,eps]) sm(); }
    intersection() { bs(); translate([0,0,eps]) assembly_head_frame() hs(); }
}
else if(CHECK=="tft_drivers") intersection() {
    // Rear cover / ESP shelf removed, as in the real service sequence.
    union() { hs(); ci(); }
    tft_points() translate([0,0,tft_pcb_z+pcb_t+0.1]) cylinder(d=driver_d,h=head_d);
}
else if(CHECK=="camera_carrier") intersection() { cc(); union() { hs(); hc(); head_references(); } }
else if(CHECK=="service_volume") intersection() {
    union() { bs(); bc(); base_references(); }
    translate(service_reserve_min) cube(service_reserve_size);
}
else if(CHECK=="tft_removal") intersection() {
    union() {
        hs(); ci(); mic_reference(); camera_reference();
        microphone_holder(); button_holder(); button_actuator();
        translate([head_w/2-wall-button_internal_l+eps,button_y,button_z]) cyl_x(button_body_d,button_internal_l-2*eps);
    }
    union() {
        // Rear cover, optional camera carrier and wiring removed.
        // Right edge stays forward; left edge comes out first.
        for(pose=[[0,tft_pcb_z+eps],[10,18],[20,27],[30,34],[40,42],[60,50],[60,65],[60,80],[60,95],[60,110]])
            translate([0,tft_y,pose[1]]) rotate([0,pose[0],0]) translate([0,-tft_y,-tft_pcb_z]) tft_reference();
    }
}
else if(CHECK=="camera_removal") intersection() {
    union() { hs(); tft_reference(); }
    union() { for(d=[eps,5,10,20,30,40,50,70]) translate([0,0,d]) ci(); }
}
else if(CHECK=="speaker_separation") {
    intersection() { speaker_reference(); union() { pi_reference(); cooler_reference(); amp_reference(); sm(); } }
    intersection() { sm(); union() { bs(); amp_reference(); } }
}
else if(CHECK=="acoustic_path") intersection() {
    union() { bs(); bc(); sm(); amp_reference(); }
    translate([speaker_xy[0],speaker_xy[1],base_panel_t+eps]) cylinder(d=speaker_acoustic_keepout_d,h=speaker_reserved_z-base_panel_t-2*eps);
}
else if(CHECK=="feet") intersection() {
    union() {
        translate([0,0,-foot_h]) bottom_speaker_slots(foot_h+floor_t);
        for(x=intake_centres_x) translate([x,intake_y,-foot_h]) rotate([0,0,90]) slot(intake_l,intake_w,foot_h+floor_t);
        for(p=base_cover_points) translate([p[0],p[1],-foot_h]) cylinder(d=m3_head_d,h=foot_h+floor_t);
    }
    feet_reference();
}
else if(CHECK=="tray_removal") {
    // The low lip is checked until entirely below the shell. The remaining
    // upper hardware/structure uses conservative CONTINUOUS vertical sweeps.
    // These boxes/cylinders enclose every intermediate position, not only samples.
    for(d=[eps,1,3,6]) intersection() {
        bs();
        translate([0,0,-d]) intersection() { bc(); translate([-100,-70,0]) cube([200,140,6]); }
    }
    intersection() {
        bs();
        union() {
            translate([speaker_xy[0]-39,speaker_xy[1]-39,-80]) cube([78,78,80+speaker_reserved_z+40]);
            translate([speaker_xy[0]-43,speaker_xy[1]-43,-80]) cube([86,86,80+speaker_mount_z+3]);
            hull() { pi_reference(); translate([0,0,-80]) pi_reference(); }
            hull() { cooler_reference(); translate([0,0,-80]) cooler_reference(); }
            hull() { amp_reference(); translate([0,0,-80]) amp_reference(); }
            // Conservative bounds include divider gussets, amp panel, rib and ties.
            translate([divider_x,divider_y[0],-80]) cube([divider_t+8,divider_y[1]-divider_y[0],80+divider_top]);
            translate([amp_xy[0]-16,amp_panel_y,-80]) cube([32,3,80+floor_t+28]);
            translate([6,-43,-80]) cube([3,86,86]);
            for(p=base_tie_points) translate([p[0]-5,p[1]-4,-80]) cube([10,8,88]);
            pi_frame() for(p=pi_holes) translate([p[0],p[1],-80-pi_origin[2]]) cylinder(d=pi_standoff_d,h=80+pi_origin[2]);
            for(p=speaker_mount_points) translate([speaker_xy[0]+p[0],speaker_xy[1]+p[1],-80]) cylinder(d=speaker_mount_boss_d,h=80+speaker_mount_z);
        }
    }
}
else if(CHECK=="speaker_removal") {
    // Tray already withdrawn: lift the speaker off its gasket, then carrier.
    for(d=[eps,2,10,30,60]) intersection() {
        union() { bc(); amp_reference(); pi_reference(); cooler_reference(); }
        translate([0,0,d]) { speaker_reference(); sm(); }
    }
}
else if(CHECK=="head_adapters") {
    intersection() { microphone_holder(); union() { hs(); hc(); cc(); tft_reference(); camera_reference(); esp_reference(); } }
    intersection() { button_holder(); union() { hs(); hc(); tft_reference(); esp_reference(); } }
    intersection() { button_actuator(); union() { hs(); button_holder(); tft_reference(); esp_reference(); } }
    intersection() { translate([-button_travel,0,0]) button_actuator(); union() { hs(); button_holder(); tft_reference(); esp_reference(); } }
    intersection() { button_reference(); union() { hs(); hc(); button_holder(); } }
}
else if(CHECK=="neck_bend") intersection() {
    union() { bs(); bc(); sm(); base_references(); }
    // Full-width 28 x 20 throat continues 8 mm below roof underside.
    translate([0,neck_y,base_h-floor_t-8]) rr(neck_cable_w,neck_cable_d,cable_edge_r,8-eps);
}
else if(CHECK=="pi_driver") intersection() {
    union() { bc(); sm(); speaker_reference(); amp_reference(); }
    pi_frame() for(p=pi_holes) translate([p[0],p[1],pcb_t+0.1]) cylinder(d=4,h=60);
}
else if(CHECK=="assertions") {
    assert(lcd_open[0] > tft_active[0] && lcd_open[1] > tft_active[1]);
    assert(tft_holes[0]/2-tft_boss_d/2 > abs(tft_glass_x)+tft_glass[0]/2);
    assert(tft_pcb_z-tft_pilot_depth > bezel_recess);
    assert(base_h-floor_t-pi_origin[2]-cooler_origin[2]-cooler_size[2] >= 20);
    assert(speaker_bay==[78,78,40]);
    assert(base_h>=62 && base_h<=64 && base_w==174 && base_d==114);
    assert(base_top_w==160 && base_top_d==102);
    assert(base_wall>=1.6 && base_panel_t>=1.6);
    assert(speaker_reserved_z-base_panel_t>=7);
    assert(speaker_reserved_z==speaker_mount_z+speaker_mount_size[2]+speaker_gasket_t);
    assert(foot_h-foot_recess>=8);
    assert(neck_cable_w>=28 && neck_cable_d>=20);
    assert(esp_shelf[0]-esp_pcb[0]>=10 && esp_shelf[1]-esp_pcb[1]>=10);
    assert(neck_cable_w>=22 && neck_cable_d>=14);
    assert(camera_y+camera_envelope[1]/2 < tft_y-tft_pcb[1]/2);
    assert(camera_front_z+camera_envelope[2] < mic_z-mic_pocket[1]/2-wall);
    echo("head_body_WHDepth",[head_w,head_h,head_d]);
    echo("base_WDH",[base_w,base_d,base_h]);
    echo("body_height",head_origin[2]+head_assembly_shift+head_h/2*cos(head_tilt));
    echo("height_with_10mm_feet",head_origin[2]+head_assembly_shift+head_h/2*cos(head_tilt)+foot_h-foot_recess);
    echo("cooler_air_height",base_h-floor_t-pi_origin[2]-cooler_origin[2]-cooler_size[2]);
    echo("neck_cable_WD",[neck_cable_w,neck_cable_d]);
    echo("TFT_active_centre_XY",[tft_active_x,tft_y]);
    echo("TFT_front_PCB_header_depths",[tft_front_z,tft_pcb_z,tft_front_z+tft_max_depth]);
    echo("minimum_head_base_gap",head_origin[2]+head_assembly_shift-head_h/2*cos(head_tilt)-head_d*sin(head_tilt)-base_h);
}
else assert(false,"Unknown CHECK");
