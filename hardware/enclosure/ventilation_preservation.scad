// Empty = exact vent cutters miss protected bosses, connector margins and roof.
// Parametric cutters avoid near-coplanar binary-STL difference slivers.
include <dimensions.scad>
use <utilities.scad>
module vent_cutters() {
    for(x=exhaust_centres_x) translate([x,base_d/2+eps,exhaust_z])
        rotate([90,0,0]) rotate([0,0,90]) slot(exhaust_l,exhaust_w,exhaust_cut_depth+2*eps);
}
module protected() {
    for(p=base_cover_points) hull() {
        translate([p[0],p[1],0]) cylinder(d=base_boss_d+2*wall,h=floor_t+base_boss_h+base_boss_ramp_h+wall);
        translate([p[0],sign(p[1])*(base_d/2-wall),0]) cylinder(d=3*wall,h=floor_t+base_boss_h+base_boss_ramp_h+wall);
    }
    translate([rear_port_x[0]-wall,base_d/2-4*wall,0])
        cube([rear_port_x[1]-rear_port_x[0]+2*wall,5*wall,rear_port_top+2*wall]);
    translate([base_w/2-4*wall,right_port_y[0]-wall,0])
        cube([5*wall,right_port_y[1]-right_port_y[0]+2*wall,right_port_top+2*wall]);
    translate([-base_w/2,-base_d/2,base_h-floor_t]) cube([base_w,base_d,floor_t]);
}
assert(exhaust_z-exhaust_l/2-rear_port_top >= 6,"Keep original 6 mm port web");
assert(base_h-floor_t-exhaust_z-exhaust_l/2 >= 2,"Keep 2 mm below roof underside");
centres = [for(x=exhaust_centres_x) x];
assert(min([for(i=[1:len(centres)-1]) centres[i]-centres[i-1]-exhaust_w]) >= 2,"Keep 2 mm vent webs");
intersection() { protected(); vent_cutters(); }
