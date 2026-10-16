include <dimensions.scad>
use <utilities.scad>
module speaker_mount() {
    translate([speaker_xy[0],speaker_xy[1],speaker_mount_z]) difference() {
        rr(speaker_mount_size[0],speaker_mount_size[1],4,speaker_mount_size[2]);
        translate([0,0,-eps]) cylinder(d=speaker_mount_open[0],h=speaker_mount_size[2]+2*eps);
        for(p=speaker_mount_points) {
            translate([p[0],p[1],-eps]) cylinder(d=m3_clear,h=speaker_mount_size[2]+2*eps);
            translate([p[0],p[1],speaker_mount_size[2]-1.7]) cylinder(d1=m3_clear,d2=6.8,h=1.7+eps);
        }
        translate([-19,-44,-eps]) cube([38,4.5,speaker_mount_size[2]+2*eps]);
        for(x=[-16,16],y=[-35,37]) translate([x,y,-eps]) slot(speaker_slot_l,speaker_slot_w,speaker_mount_size[2]+2*eps);
        for(x=[-37,37],y=[-16,16]) translate([x,y,-eps]) rotate([0,0,90]) slot(speaker_side_slot_l,speaker_slot_w,speaker_mount_size[2]+2*eps);
    }
}
module speaker_mount_print() { translate([-speaker_xy[0],-speaker_xy[1],-speaker_mount_z]) speaker_mount(); }
speaker_mount_print();
