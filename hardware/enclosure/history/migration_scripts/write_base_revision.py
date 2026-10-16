from pathlib import Path
R=Path(__file__).resolve().parent
(R/'base_shell.scad').write_text('''include <dimensions.scad>
use <utilities.scad>
module base_shell() {
    difference() {
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
''')
p=R/'base_bottom_cover.scad';s=p.read_text()
s=s.replace('rr(base_w-2*(wall+fit_normal),base_d-2*(wall+fit_normal),base_r-wall-fit_normal,base_lip_h+eps);','tapered_base_volume(0,base_lip_h,wall+fit_normal+0.3);')
s=s.replace('// Short gussets','translate([6,-43,floor_t-eps]) cube([3,86,3+eps]);\n            // Short gussets')
s=s.replace('        difference() {\n            for(x=intake_centres_x)', '''        translate([0,0,-eps]) bottom_speaker_slots(floor_t+2*eps);
        translate([divider_x-eps,7,7]) cube([divider_t+2*eps,12,8]);
        difference() {
            for(x=intake_centres_x)''')
p.write_text(s)
(R/'speaker_mount.scad').write_text('''include <dimensions.scad>
use <utilities.scad>
module speaker_mount() {
    translate([speaker_xy[0],speaker_xy[1],speaker_mount_z]) difference() {
        rr(speaker_mount_size[0],speaker_mount_size[1],4,speaker_mount_size[2]);
        translate([0,0,-eps]) cylinder(d=speaker_mount_open[0],h=speaker_mount_size[2]+2*eps);
        for(p=speaker_mount_points) {
            translate([p[0],p[1],-eps]) cylinder(d=m3_clear,h=speaker_mount_size[2]+2*eps);
            translate([p[0],p[1],speaker_mount_size[2]-1.7]) cylinder(d1=m3_clear,d2=6.8,h=1.7+eps);
        }
        for(x=[-16,16],y=[-37,37]) translate([x,y,-eps]) slot(speaker_slot_l,speaker_slot_w,speaker_mount_size[2]+2*eps);
        for(x=[-37,37],y=[-16,16]) translate([x,y,-eps]) rotate([0,0,90]) slot(speaker_side_slot_l,speaker_slot_w,speaker_mount_size[2]+2*eps);
    }
}
module speaker_mount_print() { translate([-speaker_xy[0],-speaker_xy[1],-speaker_mount_z]) speaker_mount(); }
speaker_mount_print();
''')
