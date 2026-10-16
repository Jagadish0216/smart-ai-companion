// Conservative reference envelopes, not manufacturer STEP models. NEVER export as parts.
include <dimensions.scad>
use <utilities.scad>
module tft_reference() {
    color([0.72,0.045,0.035]) difference() {
        translate([-tft_pcb[0]/2,tft_y-tft_pcb[1]/2,tft_pcb_z]) cube(tft_pcb);
        tft_points() translate([0,0,tft_pcb_z-eps]) cylinder(d=tft_hole_d,h=pcb_t+2*eps);
    }
    color(col_screen) translate([tft_glass_x-tft_glass[0]/2,tft_y-tft_glass[1]/2,tft_front_z]) cube([tft_glass[0],tft_glass[1],tft_pcb_z-tft_front_z]);
    color([0.02,0.04,0.05]) translate([tft_active_x-tft_active[0]/2,tft_y-tft_active[1]/2,tft_front_z-eps]) cube([tft_active[0],tft_active[1],eps]);
    color([0.12,0.12,0.14]) {
        translate([tft_header_side*tft_header_x-tft_header_xy[0]/2,tft_y-tft_header_xy[1]/2,tft_pcb_z+pcb_t]) cube([tft_header_xy[0],tft_header_xy[1],tft_front_z+tft_max_depth-tft_pcb_z-pcb_t]);
        // Conservative rear SMD/SD-card region, inset from mounting-hole lands.
        translate([-tft_pcb[0]/2+7,tft_y-tft_pcb[1]/2+5,tft_pcb_z+pcb_t]) cube([tft_pcb[0]-14,tft_pcb[1]-10,tft_rear_smd_depth]);
    }
}
module esp_reference() {
    color([0.06,0.12,0.18]) translate([esp_xy[0]-esp_pcb[0]/2,esp_xy[1]-esp_pcb[1]/2,esp_pcb_z]) cube(esp_pcb);
    color([0.7,0.7,0.72]) translate([esp_xy[0]-19,esp_xy[1]-8,esp_pcb_z-3]) cube([18,16,3]);
    color([0.15,0.15,0.17]) for(y=[-1,1]) translate([esp_xy[0]-esp_pcb[0]/2+3,esp_xy[1]+y*(esp_pcb[1]/2-2)-1.5,esp_header_z]) cube([esp_pcb[0]-6,3,esp_pcb_z-esp_header_z]);
}
module mic_reference() {
    color([0.10,0.32,0.65]) translate([mic_x,mic_round_y,mic_z]) cyl_y(mic_round_board_d,1.6);
    color([0.8,0.8,0.85]) translate([mic_x,mic_round_y-1,mic_z]) cyl_y(4,1);
}
module camera_reference() {
    color([0.55,0.25,0.75,0.35]) translate([-camera_envelope[0]/2,camera_y-camera_envelope[1]/2,camera_front_z]) cube(camera_envelope);
    color(col_dark) translate([0,camera_y,floor_t]) cylinder(d=camera_lens_d-1,h=camera_front_z-floor_t+4);
}
module button_reference() {
    color([0.18,0.18,0.20]) translate([tact_body_x,button_y-3,button_z-3]) cube([5,6,6]);
    color([0.5,0.5,0.5]) translate([tact_contact_x,button_y,button_z]) cyl_x(3,0.2);
}
module pi_reference() {
    pi_frame() {
        color(col_pcb) difference() {
            cube(pi_pcb);
            for(p=pi_holes) translate([p[0],p[1],-eps]) cylinder(d=2.7,h=pcb_t+2*eps);
        }
        color([0.68,0.7,0.72]) {
            for(y=pi_usb_y) translate([70,y-pi_usb_size[1]/2,pcb_t]) cube(pi_usb_size);
            translate([67,pi_eth_y-pi_eth_size[1]/2,pcb_t]) cube(pi_eth_size);
            translate([pi_power_x-pi_power_size[0]/2,-1,pcb_t]) cube(pi_power_size);
            for(x=pi_hdmi_x) translate([x-pi_hdmi_size[0]/2,-1,pcb_t]) cube(pi_hdmi_size);
        }
        color(col_dark) translate([7,49,pcb_t]) cube([51,5,9]);
    }
}
module cooler_reference() { pi_frame() color(col_cooler) translate(cooler_origin) cube(cooler_size); }
module cooler_air_reference() {
    pi_frame() color([0.2,0.7,1,0.15]) translate(cooler_origin+[0,0,cooler_size[2]]) cube([cooler_size[0],cooler_size[1],base_h-floor_t-pi_origin[2]-cooler_origin[2]-cooler_size[2]]);
}
module speaker_reference() { color(col_reserve) translate([speaker_xy[0]-speaker_bay[0]/2,speaker_xy[1]-speaker_bay[1]/2,speaker_reserved_z]) cube(speaker_bay); }
module amp_reference() {
    color([0.4,0.2,0.65]) translate([amp_xy[0]-12,amp_panel_y-2.5,7]) cube([24,2,20]);
    color([0.1,0.5,0.2]) translate([amp_xy[0]-10,amp_panel_y-8.5,9]) cube([20,6,16]);
}
module plugs_reference() {
    pi_frame() color([0.9,0.25,0.2,0.35]) {
        for(y=pi_usb_y) translate([pi_pcb[0]+3,y-usb_plug_size[1]/2,pcb_t]) cube(usb_plug_size);
        translate([pi_pcb[0]+3,pi_eth_y-eth_plug_size[1]/2,pcb_t]) cube(eth_plug_size);
        translate([pi_power_x-power_plug_size[0]/2,-power_plug_size[1],pcb_t-2]) cube(power_plug_size);
        for(x=pi_hdmi_x) translate([x-hdmi_plug_size[0]/2,-hdmi_plug_size[1],pcb_t-2]) cube(hdmi_plug_size);
    }
}
module head_references() { tft_reference(); esp_reference(); mic_reference(); camera_reference(); button_reference(); }
module base_references() { pi_reference(); cooler_reference(); speaker_reference(); amp_reference(); }
module cable_reference() { color(col_cable) translate([0,neck_y,base_h-8]) rr(neck_cable_w-4,neck_cable_d-4,cable_edge_r,28); }
module feet_reference() { color(col_dark) for(p=foot_points) translate([p[0],p[1],foot_recess-foot_h]) cylinder(d=foot_d-fit_normal,h=foot_h); }
