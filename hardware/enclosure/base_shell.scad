include <dimensions.scad>
use <utilities.scad>
module base_shell() {
    difference() {
        intersection() {
          tapered_base_volume(floor_t,base_h-eps);
          union() {
            difference() {
                tapered_base_volume(floor_t,base_h-eps);
                tapered_base_volume(floor_t-eps,base_h-floor_t,wall);
            }
            for(p=base_cover_points) {
                // Cover bosses tied to the taper; roof-down printing ramps.
                hull() {
                    translate([p[0],p[1],floor_t]) cylinder(d=base_boss_d,h=base_boss_h);
                    translate([p[0],sign(p[1])*(base_depth_at(floor_t+base_boss_h)/2-wall),floor_t]) cylinder(d=wall,h=base_boss_h);
                }
                hull() {
                    translate([p[0],p[1],floor_t+base_boss_h-eps]) cylinder(d=base_boss_d,h=eps);
                    translate([p[0],sign(p[1])*(base_depth_at(floor_t+base_boss_h+base_boss_ramp_h)/2-wall),floor_t+base_boss_h+base_boss_ramp_h]) cylinder(d=wall,h=eps);
                }
            }
        }
        }
        neck_cable_cut(base_h-floor_t-eps,base_h+eps);
        for(p=neck_points) translate([p[0],p[1],base_h-floor_t-eps]) cylinder(d=m3_clear,h=floor_t+2*eps);
        for(p=base_cover_points) translate([p[0],p[1],floor_t-eps]) cylinder(d=m3_pilot,h=m3_thread_depth+eps);
        // Full-depth cuts through the tapered and rounded skin.
        translate([rear_port_x[0],base_d/2-16,floor_t-eps]) cube([rear_port_x[1]-rear_port_x[0],20,rear_port_top-floor_t+eps]);
        translate([base_w/2-16,right_port_y[0],floor_t-eps]) cube([20,right_port_y[1]-right_port_y[0],right_port_top-floor_t+eps]);
        for(x=exhaust_centres_x) translate([x,base_d/2+eps,exhaust_z]) rotate([90,0,0]) rotate([0,0,90]) slot(exhaust_l,exhaust_w,exhaust_cut_depth+2*eps);
        translate([0,-base_d/2-eps,led_z]) cyl_y(led_hole_d,14);
    }
}
module base_shell_print() { translate([0,0,base_h]) rotate([180,0,0]) base_shell(); }
base_shell_print();
