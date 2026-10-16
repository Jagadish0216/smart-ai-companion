include <dimensions.scad>
use <utilities.scad>
CAMERA_LENS = is_undef(CAMERA_LENS) ? false : CAMERA_LENS;
CAMERA_CARRIER = is_undef(CAMERA_CARRIER) ? false : CAMERA_CARRIER;
module camera_insert(lens=false) {
    difference() {
        union() {
            translate([0,camera_y,floor_t-camera_insert_t]) cylinder(d=camera_insert_d,h=camera_insert_t+eps);
            translate([0,camera_y,floor_t]) difference() {
                cylinder(d=camera_flange_d,h=camera_insert_seat+camera_flange_t-floor_t);
                translate([0,0,-eps]) cylinder(d=camera_insert_d,h=camera_insert_seat+camera_flange_t-floor_t+2*eps);
            }
            // Rear ears sit on shell posts. Driver and screw heads pass through rail slots.
            for(x=[-camera_mount_x,camera_mount_x]) hull() {
                translate([x,camera_y+camera_tab_y,camera_insert_seat]) cylinder(d=camera_retainer_d,h=camera_flange_t);
                translate([x/2,camera_y+camera_tab_y,camera_insert_seat]) cylinder(d=5,h=camera_flange_t);
            }
        }
        if(lens) translate([0,camera_y,-eps]) cylinder(d=camera_lens_d,h=floor_t+2*eps);
        for(x=[-camera_mount_x,camera_mount_x]) translate([x,camera_y+camera_tab_y,camera_insert_seat-eps]) cylinder(d=m3_clear,h=camera_flange_t+2*eps);
    }
}
module camera_insert_print(lens=false) { translate([0,-camera_y,-(floor_t-camera_insert_t)]) camera_insert(lens); }
module camera_carrier() {
    // Optional universal backing plate; lower placement leaves the top microphone clear.
    difference() {
        rr(camera_carrier[0],camera_carrier[1],2,camera_carrier[2]);
        for(x=[-camera_mount_x,camera_mount_x],y=[-4,4]) translate([x,y,-eps]) slot(tie_slot_l,tie_slot_w,camera_carrier[2]+2*eps);
        for(x=[-14,14]) translate([x,0,-eps]) rotate([0,0,90]) slot(tie_slot_l,tie_slot_w,camera_carrier[2]+2*eps);
        translate([0,camera_carrier[1]/2,-eps]) rr(16,6,2,camera_carrier[2]+2*eps);
    }
}
if(CAMERA_CARRIER) camera_carrier(); else camera_insert_print(CAMERA_LENS);
