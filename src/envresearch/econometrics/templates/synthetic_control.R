options(warn = 2)

unit_column <- __UNIT__
time_column <- __TIME__
outcome_column <- __OUTCOME__
treated_unit <- __TREATED_UNIT__
intervention_time <- __INTERVENTION__
max_pre_rmspe <- __MAX_PRE_RMSPE__
leave_one_out_threshold <- __MAX_LOO__

input_path <- "input/data.csv"
output_root <- "output"
dir.create(output_root, showWarnings = FALSE, recursive = FALSE)
data <- utils::read.csv(input_path, check.names = FALSE, stringsAsFactors = FALSE)
units <- sort(unique(data[[unit_column]]))
times <- sort(unique(data[[time_column]]))
donors <- setdiff(units, treated_unit)
pre <- times < intervention_time
post <- times >= intervention_time
if (length(donors) < 2L || sum(pre) < 2L || !any(post)) stop("SCM support is insufficient")

wide <- matrix(NA_real_, nrow = length(units), ncol = length(times), dimnames = list(units, times))
for (i in seq_len(nrow(data))) {
  wide[as.character(data[[unit_column]][i]), as.character(data[[time_column]][i])] <- data[[outcome_column]][i]
}
if (any(!is.finite(wide))) stop("SCM panel is incomplete")
ordered <- rbind(wide[donors, , drop = FALSE], wide[treated_unit, , drop = FALSE])
set.seed(20260812)
estimate <- synthdid::synthdid_estimate(ordered, N0 = length(donors), T0 = sum(pre))
att <- as.numeric(estimate)
std_error <- sqrt(as.numeric(stats::vcov(estimate, method = "placebo")))
critical <- stats::qnorm(0.975)
utils::write.csv(data.frame(term = "ATT", estimate = att, std_error = std_error, conf_low = att - critical * std_error, conf_high = att + critical * std_error), file.path(output_root, "effect.csv"), row.names = FALSE)

omega <- as.numeric(attr(estimate, "weights")$omega)
if (length(omega) != length(donors) || any(!is.finite(omega)) || any(omega < 0)) stop("SCM weights are invalid")
omega <- omega / sum(omega)
utils::write.csv(data.frame(donor = donors, weight = omega), file.path(output_root, "weights.csv"), row.names = FALSE)
synthetic <- as.numeric(crossprod(omega, wide[donors, , drop = FALSE]))
treated <- as.numeric(wide[treated_unit, ])
gap <- treated - synthetic
gaps <- data.frame(time = times, treated = treated, synthetic = synthetic, gap = gap, period = ifelse(pre, "pre", "post"))
utils::write.csv(gaps, file.path(output_root, "gaps.csv"), row.names = FALSE)
pre_rmspe <- sqrt(mean(gap[pre]^2))
post_rmspe <- sqrt(mean(gap[post]^2))
if (pre_rmspe == 0) stop("SCM pre-period RMSPE cannot be zero")
ratio <- post_rmspe / pre_rmspe
utils::write.csv(data.frame(pre_periods = sum(pre), post_periods = sum(post), pre_rmspe = pre_rmspe, post_rmspe = post_rmspe, max_pre_rmspe = max_pre_rmspe, post_pre_ratio = ratio), file.path(output_root, "rmspe.csv"), row.names = FALSE)

fit_for <- function(target, pool) {
  matrix_target <- rbind(wide[pool, , drop = FALSE], wide[target, , drop = FALSE])
  as.numeric(synthdid::synthdid_estimate(matrix_target, N0 = length(pool), T0 = sum(pre)))
}
placebo <- data.frame(unit = donors, effect = vapply(donors, function(target) fit_for(target, setdiff(donors, target)), numeric(1)))
utils::write.csv(placebo, file.path(output_root, "placebo.csv"), row.names = FALSE)
loo_effect <- vapply(donors, function(omitted) fit_for(treated_unit, setdiff(donors, omitted)), numeric(1))
leave_one_out <- data.frame(omitted = donors, effect = loo_effect, absolute_change = abs(loo_effect - att))
utils::write.csv(leave_one_out, file.path(output_root, "leave_one_out.csv"), row.names = FALSE)
utils::write.csv(data.frame(method_id = "synthetic-control", r_version = R.version.string, package_version = as.character(utils::packageVersion("synthdid")), intervention_time = intervention_time, leave_one_out_threshold = leave_one_out_threshold), file.path(output_root, "package_configuration.csv"), row.names = FALSE)

xml_escape <- function(value) {
  value <- gsub("&", "&amp;", value, fixed = TRUE)
  value <- gsub("<", "&lt;", value, fixed = TRUE)
  value <- gsub(">", "&gt;", value, fixed = TRUE)
  gsub('"', "&quot;", value, fixed = TRUE)
}
x <- seq(55, 545, length.out = length(times))
y_min <- min(c(treated, synthetic)); y_max <- max(c(treated, synthetic))
padding <- max(y_max - y_min, max(abs(c(y_min, y_max)), 1) * 1e-6)
y_min <- y_min - padding * 0.08; y_max <- y_max + padding * 0.08
y <- function(value) 245 - (value - y_min) / (y_max - y_min) * 190
poly <- function(values, color) sprintf('<polyline fill="none" stroke="%s" points="%s"/>', color, paste(sprintf("%.3f,%.3f", x, y(values)), collapse = " "))
ticks <- paste(vapply(seq_along(times), function(i) sprintf('<g class="x-tick"><text x="%.3f" y="275">%s</text></g>', x[i], xml_escape(as.character(times[i]))), character(1)), collapse = "")
svg <- paste0('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="300" viewBox="0 0 600 300"><line x1="45" y1="250" x2="560" y2="250" stroke="black"/>', ticks, poly(treated, "#1f77b4"), poly(synthetic, "#d62728"), '</svg>')
writeLines(svg, file.path(output_root, "synthetic_control.svg"), useBytes = TRUE)
