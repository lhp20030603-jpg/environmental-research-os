options(warn = 2)

outcome_column <- __OUTCOME__
running_column <- __RUNNING__
covariate_columns <- __COVARIATES__
cutoff <- __CUTOFF__
bandwidth <- __BANDWIDTH__
donut_radius <- __DONUT_RADIUS__
cluster_column <- __CLUSTER__
confidence_level <- __CONFIDENCE__

input_path <- "input/data.csv"
output_root <- "output"
dir.create(output_root, showWarnings = FALSE, recursive = FALSE)
data <- utils::read.csv(input_path, check.names = FALSE, stringsAsFactors = FALSE)
running_internal <- data[[running_column]]
centered_internal <- running_internal - cutoff
treatment_internal <- running_internal >= cutoff
treatment_numeric <- as.numeric(treatment_internal)
outcome_internal <- data[[outcome_column]]
critical <- stats::qnorm(1 - (1 - confidence_level) / 2)

vcov_value <- if (is.null(cluster_column)) "hetero" else stats::as.formula(
  paste("~", paste0("`", cluster_column, "`"))
)

fit_cutoff <- function(response, current_bandwidth, excluded_radius = 0) {
  weight_internal <- pmax(0, 1 - abs(centered_internal) / current_bandwidth)
  keep <- abs(centered_internal) < current_bandwidth &
    abs(centered_internal) >= excluded_radius & weight_internal > 0
  left_unique <- length(unique(running_internal[keep & centered_internal < 0]))
  right_unique <- length(unique(running_internal[keep & centered_internal >= 0]))
  if (left_unique < 4 || right_unique < 4) stop("insufficient unique running values")
  local <- data.frame(
    response_internal = response[keep],
    treatment_numeric = treatment_numeric[keep],
    centered_internal = centered_internal[keep],
    weight_internal = weight_internal[keep],
    data[keep, , drop = FALSE], check.names = FALSE
  )
  model <- fixest::feols(
    response_internal ~ treatment_numeric + centered_internal +
      treatment_numeric:centered_internal,
    data = local, weights = ~weight_internal, vcov = vcov_value
  )
  table <- as.data.frame(fixest::coeftable(model))
  if (!("treatment_numeric" %in% row.names(table))) stop("RDD cutoff is unavailable")
  estimate <- table["treatment_numeric", 1]
  std_error <- table["treatment_numeric", 2]
  c(estimate, std_error, estimate - critical * std_error, estimate + critical * std_error)
}

coefficient_frame <- function(term, values) data.frame(
  term = term, estimate = values[1], std_error = values[2],
  conf_low = values[3], conf_high = values[4], check.names = FALSE
)

main <- fit_cutoff(outcome_internal, bandwidth)
utils::write.csv(
  coefficient_frame("cutoff", main), file.path(output_root, "main.csv"), row.names = FALSE
)

multipliers <- c(0.5, 1.0, 1.5)
sensitivity <- do.call(rbind, lapply(multipliers, function(multiplier) {
  values <- fit_cutoff(outcome_internal, bandwidth * multiplier)
  data.frame(
    multiplier = multiplier, term = "cutoff", estimate = values[1],
    std_error = values[2], conf_low = values[3], conf_high = values[4]
  )
}))
utils::write.csv(
  sensitivity, file.path(output_root, "bandwidth_sensitivity.csv"), row.names = FALSE
)

donut <- fit_cutoff(outcome_internal, bandwidth, donut_radius)
utils::write.csv(
  coefficient_frame("cutoff", donut), file.path(output_root, "donut.csv"), row.names = FALSE
)

continuity <- do.call(rbind, lapply(covariate_columns, function(column) {
  coefficient_frame(column, fit_cutoff(data[[column]], bandwidth))
}))
if (length(covariate_columns) == 0) {
  continuity <- data.frame(
    term = character(), estimate = numeric(), std_error = numeric(),
    conf_low = numeric(), conf_high = numeric()
  )
}
utils::write.csv(
  continuity, file.path(output_root, "covariate_continuity.csv"), row.names = FALSE
)

main_keep <- abs(centered_internal) < bandwidth
donut_keep <- main_keep & abs(centered_internal) >= donut_radius
support <- data.frame(
  observations = sum(main_keep),
  left_observations = sum(main_keep & centered_internal < 0),
  right_observations = sum(main_keep & centered_internal >= 0),
  left_unique_running = length(unique(running_internal[main_keep & centered_internal < 0])),
  right_unique_running = length(unique(running_internal[main_keep & centered_internal >= 0])),
  donut_left_observations = sum(donut_keep & centered_internal < 0),
  donut_right_observations = sum(donut_keep & centered_internal >= 0)
)
utils::write.csv(support, file.path(output_root, "support.csv"), row.names = FALSE)

plot_x <- centered_internal[main_keep]
plot_y <- outcome_internal[main_keep]
x_min <- -bandwidth
x_max <- bandwidth
y_padding <- max(diff(range(plot_y)) * 0.08, .Machine$double.eps)
y_min <- min(plot_y) - y_padding
y_max <- max(plot_y) + y_padding
map_x <- function(value) 70 + (value - x_min) / (x_max - x_min) * 600
map_y <- function(value) 270 - (value - y_min) / (y_max - y_min) * 220

breaks <- seq(x_min, x_max, length.out = 9)
bin <- cut(plot_x, breaks = breaks, include.lowest = TRUE, right = FALSE)
binned <- stats::aggregate(
  cbind(x = plot_x, y = plot_y), by = list(bin = bin), FUN = mean
)
line_for_side <- function(side) {
  keep <- if (side == "left") plot_x < 0 else plot_x >= 0
  frame <- data.frame(x = plot_x[keep], y = plot_y[keep])
  weights <- pmax(0, 1 - abs(frame$x) / bandwidth)
  fit <- stats::lm(y ~ x, data = frame, weights = weights)
  x_values <- if (side == "left") c(min(frame$x), 0) else c(0, max(frame$x))
  y_values <- stats::predict(fit, newdata = data.frame(x = x_values))
  sprintf("%.3f,%.3f %.3f,%.3f", map_x(x_values[1]), map_y(y_values[1]),
          map_x(x_values[2]), map_y(y_values[2]))
}
x_ticks <- seq(x_min, x_max, length.out = 5)
y_ticks <- seq(y_min, y_max, length.out = 5)
svg <- c(
  '<?xml version="1.0" encoding="UTF-8"?>',
  '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="320" viewBox="0 0 720 320">',
  '<rect width="100%" height="100%" fill="white"/>',
  '<line x1="70" y1="270" x2="670" y2="270" stroke="#333"/>',
  '<line x1="70" y1="50" x2="70" y2="270" stroke="#333"/>',
  sprintf('<line class="cutoff" x1="%.3f" y1="50" x2="%.3f" y2="270" stroke="#555" stroke-dasharray="5,4"/>', map_x(0), map_x(0)),
  vapply(x_ticks, function(value) sprintf(
    '<g class="x-tick"><line x1="%.3f" y1="270" x2="%.3f" y2="276" stroke="#333"/><text x="%.3f" y="292" text-anchor="middle" font-family="sans-serif" font-size="11">%.3g</text></g>',
    map_x(value), map_x(value), map_x(value), value
  ), character(1)),
  vapply(y_ticks, function(value) sprintf(
    '<g class="y-tick"><line x1="64" y1="%.3f" x2="70" y2="%.3f" stroke="#333"/><text x="60" y="%.3f" text-anchor="end" font-family="sans-serif" font-size="11">%.3g</text></g>',
    map_y(value), map_y(value), map_y(value) + 4, value
  ), character(1)),
  vapply(seq_len(nrow(binned)), function(index) sprintf(
    '<circle class="binned-mean" cx="%.3f" cy="%.3f" r="4" fill="#2369a8"/>',
    map_x(binned$x[index]), map_y(binned$y[index])
  ), character(1)),
  sprintf('<polyline class="fitted-line left" points="%s" fill="none" stroke="#b33a3a" stroke-width="2"/>', line_for_side("left")),
  sprintf('<polyline class="fitted-line right" points="%s" fill="none" stroke="#b33a3a" stroke-width="2"/>', line_for_side("right")),
  '<text x="370" y="314" text-anchor="middle" font-family="sans-serif" font-size="12">Centered running variable</text>',
  '<text x="16" y="160" text-anchor="middle" transform="rotate(-90 16 160)" font-family="sans-serif" font-size="12">Outcome</text>',
  '</svg>'
)
writeLines(svg, file.path(output_root, "rdd_plot.svg"), useBytes = TRUE)

configuration <- data.frame(
  method_id = "rdd-local-linear", r_version = R.version.string,
  fixest_version = as.character(utils::packageVersion("fixest")),
  confidence_level = confidence_level,
  cluster_column = if (is.null(cluster_column)) "" else cluster_column,
  fixed_effects = "", estimator_label = "sharp-local-linear",
  cutoff = cutoff, bandwidth = bandwidth, kernel = "triangular",
  donut_radius = donut_radius
)
utils::write.csv(
  configuration, file.path(output_root, "package_configuration.csv"), row.names = FALSE
)
