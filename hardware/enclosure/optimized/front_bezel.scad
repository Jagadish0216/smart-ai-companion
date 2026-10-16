include <dimensions.scad>
use <utilities.scad>
module front_bezel() {
    difference() {
        union() {
            translate([0,bezel_y,0]) rr(bezel_w,bezel_h,bezel_r,bezel_t);
            for(p=bezel_points) translate([p[0],p[1],bezel_t-eps]) cylinder(d=bezel_post_d,h=bezel_post_top-bezel_t+eps);
        }
        translate([tft_active_x,tft_y,-eps]) rr(lcd_open[0],lcd_open[1],lcd_corner_r,bezel_t+2*eps);
        for(p=bezel_points) translate([p[0],p[1],bezel_post_top-m3_thread_depth]) cylinder(d=m3_pilot,h=m3_thread_depth+eps);
    }
}
front_bezel();
