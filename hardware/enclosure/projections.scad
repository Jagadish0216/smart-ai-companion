// Optional undimensioned engineering silhouettes, in mm. Printed geometry only.
include <dimensions.scad>
use <utilities.scad>
VIEW = is_undef(VIEW) ? "front" : VIEW;
module view_projection() {
    if(VIEW=="front") projection(cut=false) rotate([90,0,0]) children();
    else if(VIEW=="side") projection(cut=false) rotate([90,0,0]) rotate([0,0,90]) children();
    else if(VIEW=="top") projection(cut=false) children();
    else assert(false,"Unknown VIEW");
}
view_projection() translate([0,0,base_h]) rotate([180,0,0]) import("output/base_shell.stl");
view_projection() import("output/base_bottom_cover.stl");
view_projection() assembly_head_frame() import("output/head_shell.stl");
view_projection() assembly_head_frame() translate([0,0,head_d]) rotate([180,0,0]) import("output/head_rear_cover.stl");
view_projection() assembly_head_frame() translate([0,0,bezel_seated_z]) import("output/front_bezel.stl");
view_projection() assembly_head_frame() translate([0,camera_y,floor_t-camera_insert_t]) import("output/camera_blank_insert.stl");
