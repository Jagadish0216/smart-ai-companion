include <dimensions.scad>
use <utilities.scad>
module base_bottom_cover() {
    difference() {
        union() {
            rr(base_w,base_d,base_r,floor_t);
            translate([0,0,floor_t-eps]) difference() {
                tapered_base_volume(0,base_lip_h,wall+fit_normal+0.3);
                translate([0,0,-eps]) rr(base_w-2*(wall+fit_normal+lip_t),base_d-2*(wall+fit_normal+lip_t),base_r-wall-fit_normal-lip_t,base_lip_h+2*eps);
            }
            pi_frame() for(p=pi_holes) translate([p[0],p[1],-pi_standoff_h]) cylinder(d=pi_standoff_d,h=pi_standoff_h);
            for(p=speaker_mount_points) translate([speaker_xy[0]+p[0],speaker_xy[1]+p[1],floor_t-eps]) cylinder(d=speaker_mount_boss_d,h=speaker_mount_z-floor_t+eps);
            translate([amp_xy[0],amp_panel_y,floor_t+amp_shelf[1]/2]) rotate([-90,0,0]) rr(amp_shelf[0],amp_shelf[1],2,-0+amp_shelf[2]);
            translate([divider_x,divider_y[0],floor_t-eps]) cube([divider_t,divider_y[1]-divider_y[0],divider_top-floor_t+eps]);
            translate([6,-43,floor_t-eps]) cube([3,86,3+eps]);
            // Short gussets stiffen the removable partial acoustic divider.
            for(y=[divider_y[0]+8,divider_y[1]-8]) hull() {
                translate([divider_x,y,floor_t]) cube([divider_t,wall,16]);
                translate([divider_x,y,floor_t]) cube([8+divider_t,wall,wall]);
            }
            for(p=base_tie_points) translate([p[0],p[1],floor_t-eps]) tie_block();
        }
        for(p=base_cover_points) {
            translate([p[0],p[1],-eps]) cylinder(d=m3_clear,h=floor_t+2*eps);
            hull() {
                translate([p[0],p[1],floor_t]) cylinder(d=base_boss_d+2*fit_normal,h=base_lip_h+eps);
                translate([p[0],sign(p[1])*(base_d/2-wall),floor_t]) cylinder(d=wall+2*fit_normal,h=base_lip_h+eps);
            }
        }
        pi_frame() for(p=pi_holes) translate([p[0],p[1],-pi_standoff_h+0.5]) cylinder(d=m25_pilot,h=pi_standoff_h);
        for(p=speaker_mount_points) translate([speaker_xy[0]+p[0],speaker_xy[1]+p[1],floor_t+0.5]) cylinder(d=m3_pilot,h=speaker_mount_z-floor_t);
        translate([0,0,-eps]) bottom_speaker_slots(floor_t+2*eps);
        translate([divider_x-eps,7,7]) cube([divider_t+2*eps,12,8]);
        difference() {
            for(x=intake_centres_x) translate([x,intake_y,-eps]) rotate([0,0,90]) slot(intake_l,intake_w,floor_t+2*eps);
            pi_frame() for(p=pi_holes) translate([p[0],p[1],-pi_origin[2]-1]) cylinder(d=pi_standoff_d+2*wall,h=floor_t+2);
        }
        for(p=foot_points) translate([p[0],p[1],-eps]) cylinder(d=foot_d,h=foot_recess+eps);
        for(x=[-12,12]) translate([amp_xy[0]+x,amp_panel_y-eps,17]) rotate([-90,0,0]) slot(9,3.4,amp_shelf[2]+2*eps);
    }
}
base_bottom_cover();
