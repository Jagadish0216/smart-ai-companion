// Documentation from the real exported enclosure; hardware is an explicit envelope model.
include <dimensions.scad>
use <utilities.scad>
use <components.scad>
use <head_adapters.scad>
use <companion_enclosure.scad>
VIEW = is_undef(VIEW) ? "assembly" : VIEW;
module speaker_visual() {
    translate([speaker_xy[0],speaker_xy[1],speaker_reserved_z]) {
        color([0.23,0.25,0.27]) difference() {
            rr(78,78,3,2);
            translate([0,0,-eps]) cylinder(d=40,h=2+2*eps);
        }
        color([0.10,0.11,0.12]) translate([0,0,2]) difference() {
            cylinder(d1=50,d2=35,h=19);
            translate([0,0,-eps]) cylinder(d1=39,d2=12,h=17);
        }
        color([0.42,0.44,0.46]) translate([0,0,21]) cylinder(d=36,h=19);
    }
}
module cooler_visual() {
    pi_frame() translate(cooler_origin) {
        color([0.55,0.60,0.63]) cube([70,50,3]);
        color([0.68,0.72,0.74]) for(x=[2:4:67]) translate([x,0,3]) cube([1.5,50,19]);
        color([0.12,0.14,0.16]) translate([35,25,19]) difference() {
            cylinder(d=30,h=3);
            translate([0,0,-eps]) cylinder(d=23,h=3+2*eps);
        }
    }
}
module base_visual() { pi_reference(); cooler_visual(); speaker_visual(); amp_reference(); }
module head_visual() {
    tft_reference(); esp_reference(); mic_reference(); camera_visual(); button_reference();
    color(col_dark) { microphone_holder(); button_actuator(); button_holder(); }
}
module camera_visual() {
    // Module 3 approximate PCB/lens within the separately checked 32x32x16 box.
    color([0.05,0.42,0.21]) translate([-12.5,camera_y-12,23.5]) cube([25,24,1.6]);
    color([0.04,0.045,0.05]) translate([0,camera_y,12]) cylinder(d=9,h=11.5);
    color([0.15,0.25,0.35]) translate([0,camera_y,11.95]) cylinder(d=6.5,h=0.05);
}
module label(s,p) { color([0.15,0.17,0.19]) translate(p) rotate([90,0,0]) linear_extrude(0.2) text(s,size=4,halign="center"); }
module face_art() {
    assembly_head_frame() {
        color([0.05,0.86,0.95]) for(x=[-14,14]) translate([x+tft_active_x,tft_y-3,tft_front_z-0.05]) rr(9,19,4,0.02);
        color([0.05,0.86,0.95]) translate([tft_active_x,tft_y+13,tft_front_z-0.05]) rr(18,2,1,0.02);
    }
    color([0.05,0.86,0.95]) translate([0,-base_depth_at(led_z)/2-0.2,led_z]) cyl_y(5,2);
}
module product() { shell_parts(); assembly_head_frame() head_visual(); base_visual(); feet_reference(); face_art(); }
module internal_base() {
    color(col_shell) bottom_model();
    color([0.32,0.34,0.36]) speaker_model();
    color(col_shell) difference() { base_model(); translate([-100,-100,12]) cube([200,200,100]); }
    base_visual(); feet_reference();
    color([0.2,0.7,1,0.12]) cooler_air_reference();
    color([0.8,0.25,0.65,0.5]) translate(service_reserve_min) cube(service_reserve_size);
}
module section_view() {
    color(col_shell) bottom_model();
    color([0.32,0.34,0.36]) difference() {
        speaker_model();
        translate([-65,-45,7]) cube([54,45,6]);
    }
    color(col_shell) difference() {
        base_model();
        translate([-100,-100,3]) cube([200,140,70]);
        // Keep the upper rear exhaust wall visible above the Pi/cooler section.
        translate([12,0,3]) cube([100,100,31]);
        translate([-100,-100,63]) cube([200,200,10]);
    }
    assembly_head_frame() {
        color(col_shell) difference() {
            head_model();
            translate([-head_w,-head_h,-eps]) cube([2*head_w,head_h+56,head_d-5]);
        }
        head_visual();
        color(col_dark) camera_model();
        color(col_dark) carrier_model();
        // Place actual rear panel 30 mm aside so its ESP shelf is visible.
        translate([-155,0,0]) { color(col_shell) rear_model(); esp_reference(); }
    }
    difference() {
        speaker_visual();
        translate([-80,-45,12]) cube([85,45,50]);
    }
    difference() {
        union() { pi_reference(); cooler_visual(); }
        translate([62,-40,9]) cube([30,48,35]);
    }
    amp_reference(); cooler_air_reference(); cable_reference(); feet_reference();
    // Actual underside crop and round mic as exploded detail views in the same CAD scene.
    translate([-170,0,35]) rotate([180,0,0]) color(col_shell) intersection() {
        bottom_model(); translate([-100,-70,0]) cube([200,140,3]);
    }
    translate([95,0,25]) assembly_head_frame() { mic_reference(); color(col_dark) microphone_holder(); }
    // Rear exhaust detail: exact wall crop, turned toward the viewer.
    translate([150,-10,80]) rotate([0,0,180]) translate([-47.5,-50,-49])
        color(col_shell) render(convexity=20) intersection() {
            base_model(); translate([15,40,35]) cube([65,20,28]);
        }
    label("ROUND MIC",[100,-60,225]);
    label("ESP32 / REAR COVER",[-170,-65,65]);
    label("SPEAKER GRILLE / PI INTAKE",[-170,-60,12]);
    label("CONE DOWN",[-38,-75,-5]);
    label("REAR EXHAUST",[150,-20,59]);
}
if(VIEW=="cutaway") section_view();
else if(VIEW=="base_internal") internal_base();
else if(VIEW=="exploded") exploded();
else product();
