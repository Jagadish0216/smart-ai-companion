include <dimensions.scad>
use <utilities.scad>

module head_neck() {
    world_to_head() {
        intersection() {
            translate([0,neck_y,head_mount_datum]) rr(neck_w,neck_d,neck_r,neck_top_limit-head_mount_datum);
            head_frame() translate([-head_w,head_h/2-1,-head_d]) cube([2*head_w,head_h,3*head_d]);
        }
        for(p=neck_points) translate([p[0],p[1],head_mount_datum]) cylinder(d=neck_boss_d,h=neck_boss_top-head_mount_datum);
    }
}
module head_camera_rails() {
    for(x=[-camera_mount_x,camera_mount_x]) {
        translate([x,camera_y+camera_support_y,floor_t-eps]) cylinder(d=camera_retainer_d,h=camera_bracket_z-floor_t+eps);
        translate([x,camera_y+camera_tab_y,floor_t-eps]) cylinder(d=camera_retainer_d,h=camera_insert_seat-floor_t+eps);
        translate([x,camera_y,camera_bracket_z]) difference() {
            rr(camera_rail_w,camera_envelope[1],2,camera_bracket_t);
            translate([0,0,-eps]) rotate([0,0,90]) slot(camera_slot_l,camera_slot_w,camera_bracket_t+2*eps);
            // Open the inner slot edge at the insert arms, keeping a 1.5 mm outer spine.
            translate([x>0 ? -camera_rail_w : 0,-camera_retainer_d/2-fit_normal,-eps])
                cube([camera_rail_w,camera_retainer_d+2*fit_normal,camera_bracket_t+2*eps]);
        }
    }
}
module head_mic_pocket() {
    translate([mic_x,-head_h/2+wall-eps,mic_z]) rotate([-90,0,0])
        difference() {
            rr(mic_pocket[0]+2*wall,mic_pocket[1]+2*wall,3,mic_retainer_depth);
            translate([0,0,-eps]) rr(mic_pocket[0],mic_pocket[1],1,mic_retainer_depth+2*eps);
            // Broad rear opening for cable/header exit and foam-tape access.
            translate([-mic_pocket[0]/3,-mic_pocket[1],mic_retainer_depth/2]) cube([2*mic_pocket[0]/3,mic_pocket[1],mic_retainer_depth]);
        }
}
module head_shell() {
    difference() {
        union() {
            difference() {
                rr(head_w,head_h,head_r,head_rear_z);
                translate([0,0,floor_t]) rr(head_w-2*wall,head_h-2*wall,head_r-wall,head_d);
            }
            tft_points() translate([0,0,floor_t-eps]) cylinder(d=tft_boss_d,h=tft_pcb_z-floor_t+eps);
            // Hollow towers capture the front bezel's posts with rear-access screws.
            for(p=bezel_points) translate([p[0],p[1],bezel_recess-eps]) cylinder(d=bezel_tower_d,h=bezel_tower_top-bezel_recess+eps);
            intersection() {
                rr(head_w,head_h,head_r,head_rear_z);
                for(p=head_cover_points) translate([p[0],p[1],floor_t-eps]) cylinder(d=head_cover_boss_d,h=head_rear_z-floor_t+eps);
            }
            head_camera_rails();
            head_mic_pocket();
            head_neck();
        }
        translate([0,bezel_y,-eps]) rr(bezel_w+2*fit_normal,bezel_h+2*fit_normal,bezel_r+fit_normal,bezel_recess+eps);
        translate([tft_active_x,tft_y,-eps]) rr(lcd_open[0],lcd_open[1],lcd_corner_r,floor_t+2*eps);
        tft_points() translate([0,0,tft_pcb_z-tft_pilot_depth]) cylinder(d=tft_pilot,h=tft_pilot_depth+eps);
        for(p=bezel_points) {
            translate([p[0],p[1],-eps]) cylinder(d=bezel_post_d+2*fit_precision,h=bezel_post_top+bezel_seated_z+eps);
            translate([p[0],p[1],bezel_post_top]) cylinder(d=m3_clear,h=bezel_tower_top-bezel_post_top+eps);
        }
        for(p=head_cover_points) translate([p[0],p[1],head_rear_z-m3_thread_depth]) cylinder(d=m3_pilot,h=m3_thread_depth+eps);
        // Camera insert is installed from behind and its flange covers this opening.
        translate([0,camera_y,-eps]) cylinder(d=camera_insert_d+2*fit_precision,h=floor_t+2*eps);
        for(x=[-camera_mount_x,camera_mount_x]) translate([x,camera_y+camera_tab_y,floor_t+0.3]) cylinder(d=m3_pilot,h=camera_insert_seat-floor_t);
        translate([head_w/2-wall-eps,button_y,button_z]) cyl_x(button_hole_d,wall+2*eps);
        translate([mic_x,-head_h/2-eps,mic_z]) cyl_y(mic_grille_d,wall+2*eps);
        for(a=[0:60:300]) translate([mic_x+mic_grille_radius*cos(a),-head_h/2-eps,mic_z+mic_grille_radius*sin(a)]) cyl_y(mic_grille_small_d,wall+2*eps);
        // Rearward removal path for the insert ring; pickup PCB sits above this relief.
        translate([0,camera_y,camera_insert_seat+camera_flange_t+fit_normal])
            cylinder(d=camera_flange_d+2*fit_normal,h=head_d);
        world_to_head() {
            neck_cable_cut(head_mount_datum-eps,head_mount_datum+25,head_mount_datum);
            for(p=neck_points) translate([p[0],p[1],head_mount_datum-eps]) cylinder(d=m3_pilot,h=neck_pilot_depth+eps);
        }
    }
}
head_shell();
