// Both variants use actual production meshes in one orthographic OpenSCAD view.
include <dimensions.scad>
use <utilities.scad>
module head_pose(h) { translate([0,-32,135+h-65]) rotate([-100,0,0]) children(); }
module variant(folder,h) {
    color(col_shell) {
        translate([0,0,h]) rotate([180,0,0]) import(str(folder,"base_shell.stl"));
        import(str(folder,"base_bottom_cover.stl"));
        head_pose(h) translate([0,0,68]) rotate([180,0,0]) import(str(folder,"head_rear_cover.stl"));
    }
    head_pose(h) {
        color(col_shell) render(convexity=20) difference() {
            import(str(folder,"head_shell.stl"));
            translate([-100,-100,-eps]) cube([200,220,0.3+eps]);
        }
        color(col_dark) render(convexity=20) intersection() {
            import(str(folder,"head_shell.stl"));
            translate([-100,-100,-eps]) cube([200,220,0.3+eps]);
        }
        color(col_dark) translate([0,0,0.2]) import(str(folder,"front_bezel.stl"));
        color(col_dark) translate([0,-36.5,0.6]) import(str(folder,"camera_lens_insert.stl"));
        color(col_screen) translate([-39.05,-11.98,3.48]) cube([73.44,48.96,0.02]);
        color([0.05,0.86,0.95]) {
            for(x=[-14,14]) translate([x-2.33,9.5,3.45]) rr(9,19,4,0.02);
            translate([-2.33,25.5,3.45]) rr(18,2,1,0.02);
        }
    }
    color(col_dark) for(p=foot_points) translate([p[0],p[1],-8.8]) cylinder(d=11,h=10);
    color([0.05,0.86,0.95]) translate([0,-55,27]) cyl_y(5,2);
}
translate([-118,0,0]) rotate([0,0,-20]) variant("../output/",66);
translate([118,0,0]) rotate([0,0,-20]) variant("output/",base_h);
color([0.12,0.14,0.16]) {
    translate([-118,-65,-19]) rotate([90,0,0]) linear_extrude(0.2) text("ORIGINAL 66 mm",size=9,halign="center");
    translate([118,-65,-19]) rotate([90,0,0]) linear_extrude(0.2) text(str("OPTIMIZED ",base_h," mm"),size=9,halign="center");
}
