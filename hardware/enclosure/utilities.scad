include <dimensions.scad>

module rr2(w,h,r) {
    hull() for (x=[-1,1], y=[-1,1])
        translate([x*(w/2-r),y*(h/2-r)]) circle(r=r);
}
module rr(w,h,r,t) { linear_extrude(height=t) rr2(w,h,r); }
module slot2(l,w) { hull() for (x=[-(l-w)/2,(l-w)/2]) translate([x,0]) circle(d=w); }
module slot(l,w,t) { linear_extrude(height=t) slot2(l,w); }
module head_frame() { translate(head_origin) rotate([-90-head_tilt,0,0]) children(); }
module world_to_head() { rotate([90+head_tilt,0,0]) translate(-head_origin) children(); }
module pi_frame() { translate(pi_origin) rotate([0,0,90]) children(); }
module tft_points() { for(x=[-tft_holes[0]/2,tft_holes[0]/2], y=[-tft_holes[1]/2,tft_holes[1]/2]) translate([x,tft_y+y,0]) children(); }
module cyl_y(d,h) { rotate([-90,0,0]) cylinder(d=d,h=h); }
module cyl_x(d,h) { rotate([0,90,0]) cylinder(d=d,h=h); }
module tie_block() {
    difference() {
        rr(tie_block[0],tie_block[1],2,tie_block[2]);
        translate([-tie_block[0],-tie_slot_w/2,1.5]) cube([tie_block[0]*2,tie_slot_w,2]);
    }
}
// A vertical throat with flared, radiused entry. No thin sacrificial membranes.
module neck_cable_cut(z0,z1,flare_z=base_h) {
    translate([0,neck_y,z0]) rr(neck_cable_w,neck_cable_d,cable_edge_r,z1-z0);
    translate([0,neck_y,flare_z-cable_flare]) hull() {
        rr(neck_cable_w,neck_cable_d,cable_edge_r,eps);
        translate([0,0,cable_flare]) rr(neck_cable_w+2*cable_flare,neck_cable_d+2*cable_flare,cable_edge_r+cable_flare,eps);
    }
}

module assembly_head_frame() { translate([0,0,head_assembly_shift]) head_frame() children(); }
function base_width_at(z) = base_w-(base_w-base_top_w)*max(0,z-floor_t)/base_taper_h;
function base_depth_at(z) = base_d-(base_d-base_top_d)*max(0,z-floor_t)/base_taper_h;
module tapered_base_volume(z0,z1,inset=0) {
    hull() for(z=[z0,z1]) translate([0,0,z])
        rr(base_width_at(z)-2*inset,base_depth_at(z)-2*inset,base_r-inset,eps);
}
module bottom_speaker_slots(h) {
    for(y=speaker_grille_rows)
        translate([speaker_xy[0],speaker_xy[1]+y,0])
            slot(2*sqrt(speaker_grille_radius*speaker_grille_radius-y*y),speaker_bottom_slot_w,h);
}
