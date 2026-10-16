include <dimensions.scad>
use <utilities.scad>
module base_bottom_cover() {
    difference() {
        union() {
            rr(base_w,base_d,base_r,base_panel_t);
            // 3 mm local perimeter, feet and mount lands; no full thick panel.
            difference() {
                rr(base_w,base_d,base_r,floor_t);
                translate([0,0,-eps]) rr(base_w-10,base_d-10,base_r-5,floor_t+2*eps);
            }
            for(p=foot_points) translate([p[0],p[1],0]) cylinder(d=foot_d+3,h=floor_t);
            for(p=base_cover_points) translate([p[0],p[1],0]) cylinder(d=11,h=floor_t);
            pi_frame() for(p=pi_holes) translate([p[0],p[1],-pi_origin[2]]) cylinder(d=9,h=floor_t);
            for(p=speaker_mount_points) translate([speaker_xy[0]+p[0],speaker_xy[1]+p[1],0]) cylinder(d=10,h=floor_t);
            // Sparse 1.6 mm ribs, outside the acoustic circle and Pi intake.
            for(y=[-37,37]) translate([-75,y-0.8,base_panel_t-eps]) cube([74,1.6,3+eps]);
            for(x=[-75,-1]) translate([x-0.8,-37,base_panel_t-eps]) cube([1.6,74,3+eps]);
            translate([8,-43,base_panel_t-eps]) cube([1.6,86,3+eps]);
            for(y=[-29,29.5]) translate([26,y-0.8,base_panel_t-eps]) cube([54,1.6,3+eps]);
            translate([0,0,floor_t-eps]) difference() {
                tapered_base_volume(0,base_lip_h,base_wall+fit_normal+0.3);
                translate([0,0,-eps]) rr(base_w-2*(base_wall+fit_normal+lip_t),base_d-2*(base_wall+fit_normal+lip_t),base_r-wall-fit_normal-lip_t,base_lip_h+2*eps);
            }
            pi_frame() for(p=pi_holes) translate([p[0],p[1],-pi_standoff_h]) cylinder(d=pi_standoff_d,h=pi_standoff_h);
            for(p=speaker_mount_points) translate([speaker_xy[0]+p[0],speaker_xy[1]+p[1],floor_t-eps]) cylinder(d=speaker_mount_boss_d,h=speaker_mount_z-floor_t+eps);
            translate([amp_xy[0],amp_panel_y,base_panel_t+amp_shelf[1]/2-eps]) rotate([-90,0,0]) difference() {
                rr(amp_shelf[0],amp_shelf[1],2,amp_shelf[2]);
                translate([0,0,-eps]) rr(17,20,2,amp_shelf[2]+2*eps);
            }
            translate([divider_x,divider_y[0],base_panel_t-eps]) cube([divider_t,divider_y[1]-divider_y[0],divider_top-base_panel_t+eps]);
            // The narrow divider-base rib above replaces the former 3 mm solid rib.
            // Short gussets stiffen the removable partial acoustic divider.
            for(y=[divider_y[0]+8,divider_y[1]-8]) hull() {
                translate([divider_x,y,base_panel_t-eps]) cube([divider_t,1.6,16]);
                translate([divider_x,y,base_panel_t-eps]) cube([8+divider_t,1.6,base_panel_t]);
            }
            for(p=base_tie_points) translate([p[0],p[1],base_panel_t-eps]) tie_block();
        }
        for(p=base_cover_points) {
            translate([p[0],p[1],-eps]) cylinder(d=m3_clear,h=floor_t+2*eps);
            hull() {
                translate([p[0],p[1],floor_t]) cylinder(d=base_boss_d+2*fit_normal,h=base_lip_h+eps);
                translate([p[0],sign(p[1])*(base_d/2-wall),floor_t]) cylinder(d=wall+2*fit_normal,h=base_lip_h+eps);
            }
        }
        pi_frame() for(p=pi_holes) translate([p[0],p[1],-pi_standoff_h+0.5]) cylinder(d=m25_pilot,h=pi_standoff_h);
        // Keep 4.5 mm pilot engagement despite the lowered speaker; 1.6 mm closed floor.
        for(p=speaker_mount_points) translate([speaker_xy[0]+p[0],speaker_xy[1]+p[1],1.6]) cylinder(d=m3_pilot,h=speaker_mount_z-1.6+eps);
        translate([0,0,-eps]) bottom_speaker_slots(floor_t+2*eps);
        translate([divider_x-eps,7,7]) cube([divider_t+2*eps,12,8]);
        difference() {
            for(x=intake_centres_x) translate([x,intake_y,-eps]) rotate([0,0,90]) slot(intake_l,intake_w,floor_t+2*eps);
            pi_frame() for(p=pi_holes) translate([p[0],p[1],-pi_origin[2]-1]) cylinder(d=pi_standoff_d+2*wall,h=floor_t+2);
        }
        for(p=foot_points) translate([p[0],p[1],-eps]) cylinder(d=foot_d,h=foot_recess+eps);
        // Vertical slots preserve continuous load paths along both narrow rails.
        for(x=[-12,12]) translate([amp_xy[0]+x,amp_panel_y-eps,17]) rotate([-90,0,0]) rotate([0,0,90]) slot(9,3.4,amp_shelf[2]+2*eps);
    }
}
base_bottom_cover();
