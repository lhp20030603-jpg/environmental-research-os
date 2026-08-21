options(warn = 2)

outcome_column <- __OUTCOME__
regressor_columns <- __REGRESSORS__
fixed_effect_columns <- __FIXED_EFFECTS__
unit_column <- __UNIT__
time_column <- __TIME__
cluster_column <- __CLUSTER__
confidence_level <- __CONFIDENCE__

input_path <- "input/data.csv"
output_root <- "output"
dir.create(output_root, showWarnings = FALSE, recursive = FALSE)
data <- utils::read.csv(input_path, check.names = FALSE, stringsAsFactors = FALSE)
quote_identifier <- function(value) paste0("`", value, "`")
xml_escape <- function(value) {
  value <- gsub("&", "&amp;", value, fixed = TRUE)
  value <- gsub("<", "&lt;", value, fixed = TRUE)
  value <- gsub(">", "&gt;", value, fixed = TRUE)
  value <- gsub('"', "&quot;", value, fixed = TRUE)
  gsub("'", "&apos;", value, fixed = TRUE)
}
rhs <- paste(vapply(regressor_columns, quote_identifier, character(1)), collapse = " + ")
fixed_effects <- paste(vapply(fixed_effect_columns, quote_identifier, character(1)), collapse = " + ")
formula_text <- sprintf("%s ~ %s | %s", quote_identifier(outcome_column), rhs, fixed_effects)
vcov_value <- if (is.null(cluster_column)) "hetero" else stats::as.formula(
  paste("~", quote_identifier(cluster_column))
)
model <- fixest::feols(stats::as.formula(formula_text), data = data, vcov = vcov_value)
table <- as.data.frame(fixest::coeftable(model))
terms <- row.names(table)
keep <- terms %in% regressor_columns
if (!all(regressor_columns %in% terms) || !any(keep)) stop("declared Panel FE estimand is unavailable")
critical <- stats::qnorm(1 - (1 - confidence_level) / 2)
coefficients <- data.frame(
  term = terms[keep], estimate = table[keep, 1], std_error = table[keep, 2],
  conf_low = table[keep, 1] - critical * table[keep, 2],
  conf_high = table[keep, 1] + critical * table[keep, 2], check.names = FALSE
)
utils::write.csv(coefficients, file.path(output_root, "coefficients.csv"), row.names = FALSE)

clusters <- if (is.null(cluster_column)) NA_integer_ else length(unique(data[[cluster_column]]))
support <- data.frame(
  observations = nrow(data), clusters = clusters,
  units = length(unique(data[[unit_column]])),
  time_periods = length(unique(data[[time_column]]))
)
utils::write.csv(support, file.path(output_root, "support.csv"), row.names = FALSE, na = "")
fit <- data.frame(
  r_squared = as.numeric(fixest::fitstat(model, "r2")[[1]]),
  within_r_squared = as.numeric(fixest::fitstat(model, "wr2")[[1]])
)
utils::write.csv(fit, file.path(output_root, "fit.csv"), row.names = FALSE)

axis_min <- min(c(coefficients$conf_low, 0))
axis_max <- max(c(coefficients$conf_high, 0))
axis_padding <- max((axis_max - axis_min) * 0.08, .Machine$double.eps)
axis_min <- axis_min - axis_padding
axis_max <- axis_max + axis_padding
map_x <- function(value) 160 + (value - axis_min) / (axis_max - axis_min) * 510
x_ticks <- seq(axis_min, axis_max, length.out = 5)
row_y <- function(index) 45 + index * 34
plot_height <- max(300, 150 + nrow(coefficients) * 34)
axis_y <- plot_height - 70
svg <- c(
  '<?xml version="1.0" encoding="UTF-8"?>',
  sprintf('<svg xmlns="http://www.w3.org/2000/svg" width="720" height="%.0f" viewBox="0 0 720 %.0f">', plot_height, plot_height),
  '<rect width="100%" height="100%" fill="white"/>',
  sprintf('<line x1="160" y1="%.3f" x2="670" y2="%.3f" stroke="#333"/>', axis_y, axis_y),
  sprintf('<line class="zero-line" x1="%.3f" y1="25" x2="%.3f" y2="%.3f" stroke="#777" stroke-dasharray="5,4"/>', map_x(0), map_x(0), axis_y),
  vapply(x_ticks, function(value) sprintf(
    '<g class="x-tick"><line x1="%.3f" y1="%.3f" x2="%.3f" y2="%.3f" stroke="#333"/><text x="%.3f" y="%.3f" text-anchor="middle" font-family="sans-serif" font-size="11">%.3g</text></g>',
    map_x(value), axis_y, map_x(value), axis_y + 6, map_x(value), axis_y + 22, value
  ), character(1)),
  vapply(seq_len(nrow(coefficients)), function(index) sprintf(
    '<g><text x="150" y="%.3f" text-anchor="end" font-family="sans-serif" font-size="12">%s</text><line class="confidence-interval" x1="%.3f" y1="%.3f" x2="%.3f" y2="%.3f" stroke="#2369a8" stroke-width="2"/><circle class="estimate" cx="%.3f" cy="%.3f" r="4" fill="#2369a8"/></g>',
    row_y(index) + 4, xml_escape(coefficients$term[index]),
    map_x(coefficients$conf_low[index]), row_y(index),
    map_x(coefficients$conf_high[index]), row_y(index),
    map_x(coefficients$estimate[index]), row_y(index)
  ), character(1)),
  sprintf('<text x="415" y="%.3f" text-anchor="middle" font-family="sans-serif" font-size="13">Estimate with confidence interval</text>', plot_height - 8),
  '</svg>'
)
writeLines(svg, file.path(output_root, "coefficient_plot.svg"), useBytes = TRUE)

configuration <- data.frame(
  method_id = "panel-fe", r_version = R.version.string,
  fixest_version = as.character(utils::packageVersion("fixest")),
  confidence_level = confidence_level,
  cluster_column = if (is.null(cluster_column)) "" else cluster_column,
  fixed_effects = paste(fixed_effect_columns, collapse = ";"),
  estimator_label = "fixest::feols-panel-fe",
  cutoff = "", bandwidth = "", kernel = "", donut_radius = ""
)
utils::write.csv(
  configuration, file.path(output_root, "package_configuration.csv"), row.names = FALSE
)
