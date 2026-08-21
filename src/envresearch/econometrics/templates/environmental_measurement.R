options(warn = 2)

monitor_column <- __MONITOR__
timestamp_column <- __TIMESTAMP__
value_column <- __VALUE__
unit_column <- __UNIT__
detection_flag_column <- __DETECTION_FLAG__
declared_unit <- __DECLARED_UNIT__
max_missing_rate <- __MAX_MISSING__
valid_min <- __VALID_MIN__
valid_max <- __VALID_MAX__
exceedance_threshold <- __EXCEEDANCE__

input_path <- "input/data.csv"
output_root <- "output"
dir.create(output_root, showWarnings = FALSE, recursive = FALSE)
data <- utils::read.csv(input_path, check.names = FALSE, stringsAsFactors = FALSE)
if (!all(data[[unit_column]] == declared_unit)) stop("measurement unit mismatch")
if (anyDuplicated(paste(data[[monitor_column]], data[[timestamp_column]], sep = "\r"))) {
  stop("measurement monitor-time keys are duplicated")
}
dates <- as.Date(data[[timestamp_column]])
if (any(is.na(dates))) stop("measurement timestamps must be ISO dates")
values <- data[[value_column]]
valid <- !is.na(values)
if (!any(valid)) stop("measurement data have no valid values")
if (any(values[valid] < valid_min | values[valid] > valid_max)) {
  stop("MEASUREMENT_RANGE_INVALID")
}
if (!is.null(detection_flag_column)) {
  allowed_flags <- c("", "valid", "below-detection", "above-detection")
  flags <- data[[detection_flag_column]]
  flags[is.na(flags)] <- ""
  if (any(!(flags %in% allowed_flags))) stop("MEASUREMENT_DETECTION_FLAG_INVALID")
  if (any(valid & !(flags %in% c("", "valid"))) ||
      any(!valid & !(flags %in% c("below-detection", "above-detection")))) {
    stop("MEASUREMENT_DETECTION_FLAG_INVALID")
  }
}

quantiles <- as.numeric(stats::quantile(values[valid], c(0.25, 0.5, 0.75), type = 7))
summary <- data.frame(
  mean = mean(values[valid]), minimum = min(values[valid]),
  q25 = quantiles[1], median = quantiles[2], q75 = quantiles[3],
  maximum = max(values[valid]),
  exceedances = sum(values[valid] > exceedance_threshold)
)
utils::write.csv(summary, file.path(output_root, "summary.csv"), row.names = FALSE)
completeness <- data.frame(
  total = nrow(data), valid = sum(valid), missing = sum(!valid),
  monitors = length(unique(data[[monitor_column]])),
  missing_rate = sum(!valid) / nrow(data), max_missing_rate = max_missing_rate
)
utils::write.csv(
  completeness, file.path(output_root, "completeness.csv"), row.names = FALSE
)
exceedances <- data.frame(
  threshold = exceedance_threshold, count = sum(values[valid] > exceedance_threshold)
)
utils::write.csv(exceedances, file.path(output_root, "exceedances.csv"), row.names = FALSE)

temporal_source <- data.frame(date = dates[valid], value = values[valid])
temporal <- stats::aggregate(value ~ date, data = temporal_source, FUN = mean)
names(temporal)[2] <- "mean"
utils::write.csv(temporal, file.path(output_root, "temporal.csv"), row.names = FALSE)
coverage <- do.call(rbind, lapply(sort(unique(data[[monitor_column]])), function(monitor) {
  selected <- data[[monitor_column]] == monitor
  data.frame(
    monitor = monitor, total = sum(selected), valid = sum(selected & valid),
    missing = sum(selected & !valid), check.names = FALSE
  )
}))
utils::write.csv(
  coverage, file.path(output_root, "monitor_coverage.csv"), row.names = FALSE
)

x_min <- min(dates)
x_max <- max(dates)
x_span <- max(as.numeric(x_max - x_min), 1)
y_min <- min(temporal$mean)
y_max <- max(temporal$mean)
y_padding <- max((y_max - y_min) * 0.08, max(abs(c(y_min, y_max)), 1) * 1e-6)
y_min <- y_min - y_padding
y_max <- y_max + y_padding
map_x <- function(value) 70 + as.numeric(value - x_min) / x_span * 600
map_y <- function(value) 270 - (value - y_min) / (y_max - y_min) * 220
x_ticks <- seq(x_min, x_max, length.out = 5)
y_ticks <- seq(y_min, y_max, length.out = 5)
points <- paste(vapply(seq_len(nrow(temporal)), function(index) sprintf(
  "%.3f,%.3f", map_x(temporal$date[index]), map_y(temporal$mean[index])
), character(1)), collapse = " ")
svg <- c(
  '<?xml version="1.0" encoding="UTF-8"?>',
  '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="320" viewBox="0 0 720 320">',
  '<rect width="100%" height="100%" fill="white"/>',
  '<line x1="70" y1="270" x2="670" y2="270" stroke="#333"/>',
  '<line x1="70" y1="50" x2="70" y2="270" stroke="#333"/>',
  vapply(x_ticks, function(value) sprintf(
    '<g class="x-tick"><line x1="%.3f" y1="270" x2="%.3f" y2="276" stroke="#333"/><text x="%.3f" y="292" text-anchor="middle" font-family="sans-serif" font-size="10">%s</text></g>',
    map_x(value), map_x(value), map_x(value), format(value, "%Y-%m-%d")
  ), character(1)),
  vapply(y_ticks, function(value) sprintf(
    '<g class="y-tick"><line x1="64" y1="%.3f" x2="70" y2="%.3f" stroke="#333"/><text x="60" y="%.3f" text-anchor="end" font-family="sans-serif" font-size="11">%.3g</text></g>',
    map_y(value), map_y(value), map_y(value) + 4, value
  ), character(1)),
  sprintf('<polyline class="temporal-series" points="%s" fill="none" stroke="#2369a8" stroke-width="2"/>', points),
  '</svg>'
)
writeLines(svg, file.path(output_root, "measurement_plot.svg"), useBytes = TRUE)

configuration <- data.frame(
  method_id = "environmental-measurement", r_version = R.version.string,
  declared_unit = declared_unit
)
utils::write.csv(
  configuration, file.path(output_root, "package_configuration.csv"), row.names = FALSE
)
