%% 2-DOF Pitch-Heave Flutter Analysis
% This script studies a simple 2 degree-of-freedom aeroelastic section.
% The motion variables are:
%
%   h     = heave displacement, positive upward
%   theta = pitch angle, positive counter-clockwise
%
% The model is written in second-order form:
%
%   M*q_ddot + D_a*q_dot + (K + K_a)*q = 0
%
% where q = [h; theta].

clear;
clc;
close all;

%% Parameters

p.M_plate = 6;                  % kg
p.m_wing = 2;                   % kg
p.K_plunge = 5000;              % N/m
p.K_spring = 4000;              % N/m

p.L_chord = 0.4;                % m
p.L_span = 0.5;                 % m
p.S_ref = p.L_chord * p.L_span; % m^2

p.dist_ac_hinge = 0.1;          % m
p.dist_mc_hinge = 0.1;          % m
p.dist_spring_hinge = 0.1;      % m

p.rho_air = 1.225;              % kg/m^3
p.C_L_alpha = 2*pi;             % per radian
p.C_M_alpha_dot = -1.2;

velocity_array = linspace(1, 50, 200);

%% Structural matrices

[M_sys, K_sys] = make_structural_matrices(p);

fprintf('\nMass matrix M_sys:\n');
disp(M_sys);

fprintf('Stiffness matrix K_sys:\n');
disp(K_sys);

%% Eigenvalue calculation over airspeed

eigenvalue_history = zeros(4, length(velocity_array));

for speed_index = 1:length(velocity_array)
    current_speed = velocity_array(speed_index);
    eigenvalue_history(:, speed_index) = compute_aeroelastic_roots(p, current_speed);
end

%% Figure 1: root loci
% Only the two positive-frequency roots are plotted.  The other two roots
% are their complex conjugates, so they carry the same stability information.

positive_roots = get_positive_frequency_roots(eigenvalue_history);

figure('Color', 'w');
hold on;
plot(real(positive_roots(1, :)), imag(positive_roots(1, :)), ...
    'Color', '#0072BD', 'LineWidth', 1.8, 'Marker', 'o', ...
    'MarkerIndices', 1:20:length(velocity_array));
plot(real(positive_roots(2, :)), imag(positive_roots(2, :)), ...
    'Color', '#D95319', 'LineWidth', 1.8, 'Marker', 's', ...
    'MarkerIndices', 1:20:length(velocity_array));
xline(0, '--k', 'LineWidth', 1.0);
grid on;
grid minor;
xlabel('Real part, Re(\lambda) [1/s]');
ylabel('Imaginary part, Im(\lambda) [rad/s]');
title('Eigenvalue Root Loci');
legend('Mode 1', 'Mode 2', 'Stability boundary', 'Location', 'best');
set(gca, 'FontSize', 11, 'LineWidth', 1.0);

%% Figure 2: damping and frequency trends

figure('Color', 'w');

subplot(2, 1, 1);
hold on;
root_colors = {'#0072BD', '#D95319', '#77AC30', '#7E2F8E'};
for root_number = 1:4
    plot(velocity_array, real(eigenvalue_history(root_number, :)), ...
        'Color', root_colors{root_number}, 'LineWidth', 1.4);
end
yline(0, '--k', 'LineWidth', 1.0);
grid on;
grid minor;
xlabel('Airspeed [m/s]');
ylabel('Real part [1/s]');
title('Eigenvalue Real Parts versus Airspeed');
set(gca, 'FontSize', 11, 'LineWidth', 1.0);

subplot(2, 1, 2);
hold on;
for root_number = 1:4
    plot(velocity_array, imag(eigenvalue_history(root_number, :))/(2*pi), ...
        'Color', root_colors{root_number}, 'LineWidth', 1.4);
end
grid on;
grid minor;
xlabel('Airspeed [m/s]');
ylabel('Frequency [Hz]');
title('Eigenvalue Imaginary Parts versus Airspeed');
set(gca, 'FontSize', 11, 'LineWidth', 1.0);

%% Figure 3: spring position study

spring_factors = [0.9, 1.0, 1.1];
case_names = {'Spring -10%', 'Baseline', 'Spring +10%'};
case_colors = {'#A2142F', '#0072BD', '#77AC30'};

figure('Color', 'w');
hold on;

for case_number = 1:length(spring_factors)
    p_case = p;
    p_case.dist_spring_hinge = p.dist_spring_hinge * spring_factors(case_number);

    case_roots = zeros(4, length(velocity_array));
    for speed_index = 1:length(velocity_array)
        case_roots(:, speed_index) = compute_aeroelastic_roots(p_case, velocity_array(speed_index));
    end

    max_real_part = max(real(case_roots), [], 1);

    plot(velocity_array, max_real_part, ...
        'Color', case_colors{case_number}, ...
        'LineWidth', 1.8, ...
        'DisplayName', case_names{case_number});
end

yline(0, '--k', 'LineWidth', 1.0, 'DisplayName', 'Stability boundary');
grid on;
grid minor;
xlabel('Airspeed [m/s]');
ylabel('Maximum real part [1/s]');
title('Effect of Spring Position on Flutter Speed');
legend('Location', 'best');
set(gca, 'FontSize', 11, 'LineWidth', 1.0);

%% Estimate flutter speed for the baseline case

max_real_baseline = max(real(eigenvalue_history), [], 1);
crossing_index = find(max_real_baseline(1:end-1) <= 0 & max_real_baseline(2:end) > 0, ...
    1, 'first');

if ~isempty(crossing_index)
    speed_1 = velocity_array(crossing_index);
    speed_2 = velocity_array(crossing_index + 1);
    growth_1 = max_real_baseline(crossing_index);
    growth_2 = max_real_baseline(crossing_index + 1);

    flutter_speed = speed_1 - growth_1*(speed_2 - speed_1)/(growth_2 - growth_1);
    fprintf('\nApproximate flutter speed: %.2f m/s\n', flutter_speed);
elseif max_real_baseline(1) > 0
    fprintf('\nThe system is already unstable at %.2f m/s in this model.\n', velocity_array(1));
else
    fprintf('\nNo flutter detected in the selected velocity range.\n');
end

%% Local functions

function [M_sys, K_sys] = make_structural_matrices(p)
% Build the structural mass and stiffness matrices.

total_mass = p.M_plate + p.m_wing;

% Approximate wing pitch inertia about the hinge.
wing_inertia_about_cg = p.m_wing * p.L_chord^2 / 12;
wing_inertia_about_hinge = wing_inertia_about_cg + p.m_wing * p.dist_mc_hinge^2;

M_sys = [total_mass, p.m_wing*p.dist_mc_hinge; ...
         p.m_wing*p.dist_mc_hinge, wing_inertia_about_hinge];

pitch_stiffness = p.K_spring * p.dist_spring_hinge^2;

K_sys = [p.K_plunge, 0; ...
         0, pitch_stiffness];
end

function roots = compute_aeroelastic_roots(p, airspeed)
% Compute the four state-space eigenvalues at one airspeed.

[M_sys, K_sys] = make_structural_matrices(p);

% Aerodynamic damping matrix, written in the same form as the given formula.
S = p.S_ref;
c = p.L_chord;
x_ac = p.dist_ac_hinge;
rho = p.rho_air;
CL_alpha = p.C_L_alpha;
CM_alpha_dot = p.C_M_alpha_dot;

D_a = 0.5 * rho * airspeed * ...
    [-S*CL_alpha,        -S*x_ac*CL_alpha; ...
     -S*x_ac*CL_alpha,   -S*c^2*CM_alpha_dot];

dynamic_pressure = 0.5 * p.rho_air * airspeed^2;
lift_slope = dynamic_pressure * p.S_ref * p.C_L_alpha;

K_a = [0, -lift_slope; ...
       0, -p.dist_ac_hinge*lift_slope];

A = [zeros(2), eye(2); ...
     -M_sys\(K_sys + K_a), -M_sys\D_a];

roots = eig(A);
end

function positive_roots = get_positive_frequency_roots(eigenvalue_history)
% Select the two roots with positive imaginary parts at each speed.

positive_roots = zeros(2, size(eigenvalue_history, 2));

for speed_index = 1:size(eigenvalue_history, 2)
    roots_at_speed = eigenvalue_history(:, speed_index);
    roots_at_speed = roots_at_speed(imag(roots_at_speed) >= 0);

    [~, order] = sort(imag(roots_at_speed));
    positive_roots(:, speed_index) = roots_at_speed(order(1:2));
end
end
