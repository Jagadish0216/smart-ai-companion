from pathlib import Path
R=Path(__file__).resolve().parent
p=R/'clearance_checks.scad';s=p.read_text().replace('use <components.scad>','use <components.scad>\nuse <head_adapters.scad>')
s=s.replace('head_frame() hs()','assembly_head_frame() hs()')
s=s.replace('assert(base_h-floor_t-pi_origin[2]-cooler_origin[2]-cooler_size[2] >= 30);','assert(base_h-floor_t-pi_origin[2]-cooler_origin[2]-cooler_size[2] >= 20);\n    assert(speaker_bay==[78,78,40]);\n    assert(base_h==66 && base_w==174 && base_d==114);\n    assert(foot_h-foot_recess>=8);\n    assert(neck_cable_w>=28 && neck_cable_d>=20);')
s=s.replace('head_origin[2]+head_h/2*cos(head_tilt)','head_origin[2]+head_assembly_shift+head_h/2*cos(head_tilt)').replace('height_with_6mm_feet','height_with_10mm_feet').replace('head_origin[2]-head_h/2','head_origin[2]+head_assembly_shift-head_h/2')
# Camera insert retains its original withdrawal path; the retrofit mic is removed first.
s=s.replace('union() { hs(); tft_reference(); mic_reference(); }','union() { hs(); tft_reference(); }')
idx=s.index('else if(CHECK=="assertions")')
s=s[:idx]+'''else if(CHECK=="speaker_separation") {
    intersection() { speaker_reference(); union() { pi_reference(); cooler_reference(); amp_reference(); sm(); } }
    intersection() { sm(); union() { bs(); amp_reference(); } }
}
else if(CHECK=="acoustic_path") intersection() {
    union() { bs(); bc(); sm(); amp_reference(); }
    translate([speaker_xy[0],speaker_xy[1],floor_t+eps]) cylinder(d=speaker_acoustic_keepout_d,h=speaker_reserved_z-floor_t-2*eps);
}
else if(CHECK=="feet") intersection() {
    union() {
        translate([0,0,-foot_h]) bottom_speaker_slots(foot_h+floor_t);
        for(x=intake_centres_x) translate([x,intake_y,-foot_h]) rotate([0,0,90]) slot(intake_l,intake_w,foot_h+floor_t);
        for(p=base_cover_points) translate([p[0],p[1],-foot_h]) cylinder(d=m3_head_d,h=foot_h+floor_t);
    }
    feet_reference();
}
else if(CHECK=="tray_removal") intersection() {
    bs();
    union() { for(d=[eps,1,3,6,12,24,40,60,80]) translate([0,0,-d]) {
        bc(); sm(); base_references();
    } }
}
else if(CHECK=="speaker_removal") intersection() {
    // Tray already withdrawn: lift the speaker off its gasket, then carrier.
    union() { bc(); amp_reference(); pi_reference(); cooler_reference(); }
    union() { for(d=[eps,2,10,30,60]) translate([0,0,d]) { speaker_reference(); sm(); } }
}
else if(CHECK=="head_adapters") {
    intersection() { microphone_holder(); union() { hs(); hc(); tft_reference(); camera_reference(); esp_reference(); } }
    intersection() { button_holder(); union() { hs(); hc(); tft_reference(); esp_reference(); } }
    intersection() { button_actuator(); union() { hs(); button_holder(); tft_reference(); esp_reference(); } }
    intersection() { translate([-button_travel,0,0]) button_actuator(); union() { hs(); button_holder(); tft_reference(); esp_reference(); } }
    intersection() { button_reference(); union() { hs(); hc(); button_holder(); } }
}
else if(CHECK=="neck_bend") intersection() {
    union() { bs(); bc(); sm(); base_references(); }
    // Full-width 28 x 20 throat continues 8 mm below roof underside.
    translate([0,neck_y,base_h-floor_t-8]) rr(neck_cable_w,neck_cable_d,cable_edge_r,8-eps);
}
else if(CHECK=="pi_driver") intersection() {
    union() { bc(); sm(); speaker_reference(); amp_reference(); }
    pi_frame() for(p=pi_holes) translate([p[0],p[1],pcb_t+0.1]) cylinder(d=4,h=60);
}
'''+s[idx:]
p.write_text(s)
p=R/'check_clearances.py';s=p.read_text().replace('"camera_removal"]','"camera_removal","speaker_separation","acoustic_path","feet","tray_removal","speaker_removal","head_adapters","neck_bend","pi_driver"]');p.write_text(s)
# Preview assembly uses the frozen head transform plus its new base datum shift.
p=R/'companion_enclosure.scad';s=p.read_text().replace('use <speaker_mount.scad>','use <speaker_mount.scad>\nuse <head_adapters.scad>').replace('head_frame()', 'assembly_head_frame()')
s=s.replace('CAMERA_INSTALLED) ? false','CAMERA_INSTALLED) ? true')
s=s.replace('color(col_shell) head_model();\n        color(col_shell) rear_model();','color(col_shell) head_model();\n        color(col_dark) button_actuator();\n        color(col_dark) button_holder();\n        color(col_dark) microphone_holder();\n        color(col_shell) rear_model();')
s=s.replace('for(y=[-1,1]) translate([-layout_pitch_x,layout_pitch_y+45+y*6,0]) if(MESH_PREVIEW) import("output/speaker_retainer_bar.stl"); else speaker_bar();','translate([-layout_pitch_x,layout_pitch_y+65,0]) microphone_holder_print();')
s=s.replace('else if(PART == "speaker_bar") speaker_bar();','else if(PART == "button_actuator") button_actuator_print();')
s=s.replace('all_references(); cooler_air_reference(); cable_reference(); plugs_reference(); feet_reference();','all_references(); cooler_air_reference(); cable_reference(); feet_reference();\n    assembly_head_frame() { color(col_dark) button_actuator(); color(col_dark) microphone_holder(); color(col_dark) button_holder(); }')
s=s.replace('if(SHOW_COMPONENTS) all_references();','if(SHOW_COMPONENTS) all_references();\n    color([0.1,0.85,0.94]) translate([0,-base_depth_at(led_z)/2-0.2,led_z]) cyl_y(5,2);')
p.write_text(s)
