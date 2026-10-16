include <dimensions.scad>
use <utilities.scad>
module head_rear_cover() {
    difference() {
        union() {
            translate([0,0,head_rear_z]) rr(head_w,head_h,head_r,head_cover_t);
            translate([0,0,head_rear_z-lip_h]) difference() {
                rr(head_w-2*(wall+fit_normal),head_h-2*(wall+fit_normal),head_r-wall-fit_normal,lip_h+eps);
                translate([0,0,-eps]) rr(head_w-2*(wall+fit_normal+lip_t),head_h-2*(wall+fit_normal+lip_t),head_r-wall-fit_normal-lip_t,lip_h+2*eps);
            }
            // Broad shelf on the removable cover: 5 mm clearance around nominal PCB.
            translate([esp_xy[0],esp_xy[1],esp_shelf_z]) rr(esp_shelf[0],esp_shelf[1],3,head_rear_z-esp_shelf_z+eps);
        }
        for(p=head_cover_points) {
            translate([p[0],p[1],head_rear_z-lip_h-eps]) cylinder(d=head_cover_boss_d+2*fit_normal,h=lip_h+eps);
            translate([p[0],p[1],head_rear_z-eps]) cylinder(d=m3_clear,h=head_cover_t+2*eps);
        }
        for(x=head_vent_xs) translate([x,head_vent_y,head_rear_z-eps]) rotate([0,0,90]) slot(head_vent_l,head_vent_w,head_cover_t+2*eps);
        for(x=esp_tie_offsets,y=[-1,1]) translate([esp_xy[0]+x,esp_xy[1]+y*(esp_shelf[1]/2-2.5),esp_shelf_z-eps]) slot(tie_slot_l,tie_slot_w,head_d-esp_shelf_z+2*eps);
    }
}
module head_rear_cover_print() { translate([0,0,head_d]) rotate([180,0,0]) head_rear_cover(); }
head_rear_cover_print();
