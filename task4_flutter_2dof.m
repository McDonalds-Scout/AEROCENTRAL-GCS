%% 2-DOF plunge-pitch aeroelastic flutter analysis
% Coordinates are measured from the leading edge (LE). Dynamic matrices use
% lever arms measured relative to the elastic axis / hinge point d.

clear; clc; close all;

%% 1. Geometry and lever arms
x_a = 0.075;     % m, LE to aerodynamic center a
x_b = 0.120;     % m, LE to spring attachment b
x_c = 0.150;     % m, LE to center of gravity c
x_d = 0.180;     % m, LE to hinge / elastic axis d

x_ea      = x_d - x_a;  % m, elastic axis to aerodynamic center
x_cg      = x_d - x_c;  % m, CG offset from elastic axis
x_spring  = x_d - x_b;  % m, spring lever arm from elastic axis
x_a_lever = x_ea;       % m, aerodynamic control-point lever arm

%% 2. System parameters from Table 4.1
m_t     = 5.0;          % kg
I_alpha = 0.1;          % kg*m^2
c       = 0.3;          % m
rho     = 1.225;        % kg/m^3
C_La    = 2*pi;         % 1/rad

target_freq = 5;        % Hz
omega_5hz   = 2*pi*target_freq;

K_h     = 2 * (0.5 * m_t * omega_5hz^2);
K_alpha = I_alpha * omega_5hz^2;
k_alpha = K_alpha / (2*x_spring^2); %#ok<NASGU>

%% 3. Structural matrices
M = [m_t,          m_t*x_cg;
     m_t*x_cg,     I_alpha];

K = [K_h,          0;
     0,            K_alpha];

%% 4. Wind-speed sweep and state-space eigensolution
U_vec = 0.1:0.1:40;       % m/s
nU = numel(U_vec);
nModes = 2;

eig_all = zeros(4, nU);
mode_lambda = zeros(nModes, nU);
mode_shape = zeros(4, nModes, nU);

for iU = 1:nU
    U = U_vec(iU);
    q_inf = 0.5*rho*U^2;

    C_aero = -(q_inf*c*C_La/U) * ...
        [1,      x_a_lever;
         x_ea,   x_ea*x_a_lever];

    K_aero = q_inf*c*C_La * ...
        [0,      1;
         0,      x_ea];

    A = [zeros(2), eye(2);
         -M\(K - K_aero), -M\C_aero];

    [V, D] = eig(A);
    lambda = diag(D);
    eig_all(:, iU) = lambda;

    % Use the two upper-half-plane roots at the first speed as physical
    % modal representatives, then continue by eigenvector correlation.
    if iU == 1
        upper_idx = find(imag(lambda) >= -1e-10);
        if numel(upper_idx) < nModes
            [~, upper_idx] = maxk(imag(lambda), nModes);
        end

        [~, order] = sort(abs(imag(lambda(upper_idx))), 'ascend');
        chosen = upper_idx(order(1:nModes));

        mode_lambda(:, iU) = lambda(chosen);
        mode_shape(:, :, iU) = normalize_columns(V(:, chosen));
    else
        prev_shape = mode_shape(:, :, iU-1);
        curr_shape = normalize_columns(V);

        mac = abs(prev_shape' * curr_shape);
        pairs = best_two_mode_assignment(mac);
        chosen = pairs(:, 2);

        mode_lambda(:, iU) = lambda(chosen);
        mode_shape(:, :, iU) = curr_shape(:, chosen);

        % Keep the plotted representative in the upper half-plane when the
        % mode is oscillatory. This preserves positive modal frequencies.
        for iMode = 1:nModes
            if imag(mode_lambda(iMode, iU)) < -1e-9
                mode_lambda(iMode, iU) = conj(mode_lambda(iMode, iU));
                mode_shape(:, iMode, iU) = conj(mode_shape(:, iMode, iU));
            end
        end
    end
end

growth = real(mode_lambda);
freq_hz = abs(imag(mode_lambda))/(2*pi);

%% 5. Critical-speed identification
[U_f, flutter_mode] = first_zero_crossing(U_vec, growth, "positive");
flutter_below_sweep = false;
if isnan(U_f) && any(growth(:, 1) >= 0)
    flutter_below_sweep = true;
    [~, flutter_mode] = max(growth(:, 1));
end

% Static divergence is identified from the same state-space eigenstructure:
% at divergence one modal frequency collapses to zero. The determinant of
% K_eff = K - K_aero is also monitored to interpolate the zero robustly.
det_Keff = zeros(1, nU);
for iU = 1:nU
    U = U_vec(iU);
    q_inf = 0.5*rho*U^2;
    K_aero = q_inf*c*C_La * [0, 1; 0, x_ea];
    det_Keff(iU) = det(K - K_aero);
end

[U_d, ~] = first_zero_crossing(U_vec, det_Keff, "negative");
if isnan(U_d)
    [U_d, divergence_mode] = first_frequency_collapse(U_vec, freq_hz);
else
    [~, divergence_mode] = min(abs(freq_hz(:, closest_index(U_vec, U_d))));
end

%% 6. Console output
fprintf('2-DOF plunge-pitch aeroelastic analysis\n');
fprintf('---------------------------------------\n');
fprintf('Lever arms: x_ea = %.4f m, x_cg = %.4f m, x_spring = %.4f m\n', ...
    x_ea, x_cg, x_spring);
fprintf('K_h = %.4f N/m, K_alpha = %.4f N*m/rad\n', K_h, K_alpha);

if flutter_below_sweep
    fprintf(['Flutter velocity U_f: no negative-to-positive crossing in sweep; ', ...
        'mode %d is already unstable at U = %.3f m/s, so U_f <= %.3f m/s\n'], ...
        flutter_mode, U_vec(1), U_vec(1));
elseif isnan(U_f)
    fprintf('Flutter velocity U_f: not found in %.1f to %.1f m/s\n', U_vec(1), U_vec(end));
else
    fprintf('Flutter velocity U_f: %.3f m/s (mode %d)\n', U_f, flutter_mode);
end

if isnan(U_d)
    fprintf('Divergence velocity U_d: not found in %.1f to %.1f m/s\n', U_vec(1), U_vec(end));
else
    fprintf('Divergence velocity U_d: %.3f m/s (mode %d)\n', U_d, divergence_mode);
end

%% 7. Figure 4.2: V-f and V-g diagrams
figure('Name', 'Figure 4.2 - 2-DOF Flutter Analysis', 'Color', 'w');

subplot(2, 1, 1);
plot(U_vec, freq_hz(1, :), 'b-', 'LineWidth', 1.5); hold on;
plot(U_vec, freq_hz(2, :), 'r-', 'LineWidth', 1.5);
grid on; box on;
xlabel('Wind speed U (m/s)');
ylabel('Frequency f (Hz)');
title('Figure 4.2(a): V-f diagram');
legend('Mode 1', 'Mode 2', 'Location', 'best');

if ~isnan(U_d)
    f_ud = interp1(U_vec, freq_hz(divergence_mode, :), U_d, 'linear', 'extrap');
    plot(U_d, f_ud, 'ko', 'MarkerFaceColor', 'y', 'MarkerSize', 7);
    xline(U_d, 'k--', sprintf(' U_d = %.2f m/s', U_d), ...
        'LabelVerticalAlignment', 'bottom');
end

subplot(2, 1, 2);
plot(U_vec, growth(1, :), 'b-', 'LineWidth', 1.5); hold on;
plot(U_vec, growth(2, :), 'r-', 'LineWidth', 1.5);
yline(0, 'k--', 'LineWidth', 1.0);
grid on; box on;
xlabel('Wind speed U (m/s)');
ylabel('Real part of eigenvalue (1/s)');
title('Figure 4.2(b): V-g diagram');
legend('Mode 1', 'Mode 2', 'Re(\lambda)=0', 'Location', 'best');

if ~isnan(U_f)
    g_uf = interp1(U_vec, growth(flutter_mode, :), U_f, 'linear', 'extrap');
    plot(U_f, g_uf, 'ko', 'MarkerFaceColor', 'g', 'MarkerSize', 7);
    xline(U_f, 'g--', sprintf(' U_f = %.2f m/s', U_f), ...
        'LabelVerticalAlignment', 'bottom');
end

if ~isnan(U_d)
    g_ud = interp1(U_vec, growth(divergence_mode, :), U_d, 'linear', 'extrap');
    plot(U_d, g_ud, 'ks', 'MarkerFaceColor', 'y', 'MarkerSize', 7);
    xline(U_d, 'k--', sprintf(' U_d = %.2f m/s', U_d), ...
        'LabelVerticalAlignment', 'top');
end

%% Local helper functions
function Vn = normalize_columns(V)
    Vn = V;
    for k = 1:size(V, 2)
        nk = norm(V(:, k));
        if nk > 0
            Vn(:, k) = V(:, k)/nk;
        end
    end
end

function pairs = best_two_mode_assignment(score)
    % Exhaustive two-mode assignment from a 2-by-4 score matrix. The output
    % is [previous_mode, current_eigenvalue_index] for the best unique pair.
    best_score = -Inf;
    pairs = [1, 1; 2, 2];
    for i = 1:size(score, 2)
        for j = 1:size(score, 2)
            if j == i
                continue;
            end
            s = score(1, i) + score(2, j);
            if s > best_score
                best_score = s;
                pairs = [1, i; 2, j];
            end
        end
    end
end

function [Ucrit, mode_id] = first_zero_crossing(U, y, direction)
    Ucrit = NaN;
    mode_id = NaN;

    for iMode = 1:size(y, 1)
        yi = y(iMode, :);
        for k = 2:numel(U)
            crosses_positive = strcmp(direction, "positive") && ...
                yi(k-1) < 0 && yi(k) >= 0;
            crosses_negative = strcmp(direction, "negative") && ...
                yi(k-1) > 0 && yi(k) <= 0;

            if crosses_positive || crosses_negative
                Ucandidate = interp_zero(U(k-1), U(k), yi(k-1), yi(k));
                if isnan(Ucrit) || Ucandidate < Ucrit
                    Ucrit = Ucandidate;
                    mode_id = iMode;
                end
                break;
            end
        end
    end
end

function [Ucrit, mode_id] = first_frequency_collapse(U, freq_hz)
    freq_tol = 1e-3;
    Ucrit = NaN;
    mode_id = NaN;

    for iMode = 1:size(freq_hz, 1)
        fi = freq_hz(iMode, :);
        idx = find(fi <= freq_tol, 1, 'first');
        if ~isempty(idx) && idx > 1
            Ucandidate = interp_zero(U(idx-1), U(idx), fi(idx-1), fi(idx));
            if isnan(Ucrit) || Ucandidate < Ucrit
                Ucrit = Ucandidate;
                mode_id = iMode;
            end
        end
    end
end

function U0 = interp_zero(U1, U2, y1, y2)
    if abs(y2 - y1) < eps
        U0 = U2;
    else
        U0 = U1 - y1*(U2 - U1)/(y2 - y1);
    end
end

function idx = closest_index(U, Utarget)
    [~, idx] = min(abs(U - Utarget));
end
