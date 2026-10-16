// Removable retrofit pieces: accepted head-shell geometry is unchanged.
include <dimensions.scad>
use <utilities.scad>
ADAPTER = is_undef(ADAPTER) ? "microphone" : ADAPTER;
module microphone_holder_print() {
    difference() {
        union() {
            cylinder(d=31,h=5);
            for(x=[-17,17]) translate([x,0,0]) rr(6,14,2,3);
        }
        translate([0,0,-eps]) cylinder(d=21,h=5+2*eps);
        translate([0,0,2]) cylinder(d=mic_round_pocket_d,h=3+eps);
        for(x=[-17,17]) translate([x,0,-eps]) rotate([0,0,90]) slot(8,3.4,3+2*eps);
        translate([-17,-5,3.5]) cube([7,10,2]);
    }
}
module microphone_holder() {
    translate([mic_x,mic_adapter_y,mic_z]) rotate([-90,0,0]) microphone_holder_print();
}
module button_actuator_print() {
    cylinder(d=button_actuator_d,h=4.2);
    translate([0,0,4.2-eps]) cylinder(d=button_actuator_flange_d,h=1.5+eps);
    translate([0,0,5.7-eps]) cylinder(d=4,h=5.1+eps);
}
module button_actuator() {
    translate([head_w/2+1,button_y,button_z]) rotate([0,-90,0]) button_actuator_print();
}
module button_holder_print() {
    // Universal tape/shim mount, not an exact snap-fit to an unmeasured switch.
    difference() {
        union() {
            rr(23,20,3,3);
            for(x=[-10,10]) translate([x,0,0]) rr(3,20,1,14);
        }
        translate([-5,-5,2]) cube([10,10,2]);
        for(y=[-7,7]) translate([0,y,-eps]) slot(11,2,3+2*eps);
    }
}
module button_holder() {
    translate([52,button_y,button_z]) rotate([0,90,0]) button_holder_print();
}
if(ADAPTER=="microphone") microphone_holder_print();
else if(ADAPTER=="button_actuator") button_actuator_print();
else if(ADAPTER=="button_holder") button_holder_print();
