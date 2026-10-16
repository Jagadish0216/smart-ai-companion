// Actual clear area normal to each vented face, using current exported meshes.
// Projection removes any retained skin/lands through the complete wall depth.
include <dimensions.scad>
use <utilities.scad>
AREA = is_undef(AREA) ? "exhaust" : AREA;
module base_mesh() { translate([0,0,base_h]) rotate([180,0,0]) import("output/base_shell.stl"); }
module intake_nominal() {
    for(x=intake_centres_x) translate([x,intake_y]) rotate(90) slot2(intake_l,intake_w);
}
module exhaust_nominal() {
    for(x=exhaust_centres_x) translate([x,exhaust_z]) rotate(90) slot2(exhaust_l,exhaust_w);
}
if(AREA=="intake") difference() {
    intake_nominal();
    projection(cut=false) intersection() {
        import("output/base_bottom_cover.stl");
        translate([-base_w/2,-base_d/2,0]) cube([base_w,base_d,floor_t]);
    }
}
else if(AREA=="exhaust") difference() {
    exhaust_nominal();
    projection(cut=false) rotate([-90,0,0]) intersection() {
        base_mesh();
        translate([-base_w/2,base_d/2-4*wall,exhaust_z-exhaust_l/2])
            cube([base_w,4*wall+eps,exhaust_l]);
    }
}
else assert(false,"Unknown AREA");
