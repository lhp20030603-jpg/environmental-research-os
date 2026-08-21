options(warn = 2)

outcome_column <- __OUTCOME__
endogenous_columns <- __ENDOGENOUS__
instrument_columns <- __INSTRUMENTS__
control_columns <- __CONTROLS__
fixed_effect_columns <- __FIXED_EFFECTS__
cluster_column <- __CLUSTER__
confidence_level <- __CONFIDENCE__
weak_threshold <- __WEAK_THRESHOLD__

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
join_terms <- function(values, empty = "1") {
  if (length(values)) paste(vapply(values, quote_identifier, character(1)), collapse = " + ") else empty
}
controls <- join_terms(control_columns)
fixed_effects <- join_terms(fixed_effect_columns, "0")
endogenous <- join_terms(endogenous_columns)
instruments <- join_terms(instrument_columns)
iv_formula <- stats::as.formula(sprintf(
  "%s ~ %s | %s | %s ~ %s", quote_identifier(outcome_column), controls,
  fixed_effects, endogenous, instruments
))
vcov_value <- if (is.null(cluster_column)) "hetero" else stats::as.formula(
  paste("~", quote_identifier(cluster_column))
)
model <- fixest::feols(iv_formula, data = data, vcov = vcov_value)
critical <- stats::qnorm(1 - (1 - confidence_level) / 2)
normalize <- function(table, requested, prefix = "") {
  terms <- row.names(table)
  normalized <- if (nzchar(prefix)) sub(paste0("^", prefix), "", terms) else terms
  keep <- normalized %in% requested
  if (!all(requested %in% normalized)) stop("declared IV coefficient is unavailable")
  data.frame(
    term = normalized[keep], estimate = table[keep, 1], std_error = table[keep, 2],
    conf_low = table[keep, 1] - critical * table[keep, 2],
    conf_high = table[keep, 1] + critical * table[keep, 2], check.names = FALSE
  )
}
structural <- normalize(as.data.frame(fixest::coeftable(model)), endogenous_columns, "fit_")
utils::write.csv(structural, file.path(output_root, "structural.csv"), row.names = FALSE)

first_stage <- do.call(rbind, lapply(endogenous_columns, function(endogenous_name) {
  first_formula <- stats::as.formula(sprintf(
    "%s ~ %s | %s", quote_identifier(endogenous_name),
    join_terms(c(instrument_columns, control_columns)), fixed_effects
  ))
  first_model <- fixest::feols(first_formula, data = data, vcov = vcov_value)
  coefficients <- stats::coef(first_model)
  covariance <- stats::vcov(first_model)
  if (!all(instrument_columns %in% names(coefficients))) {
    stop("declared first-stage instrument is unavailable")
  }
  beta <- coefficients[instrument_columns]
  covariance <- covariance[instrument_columns, instrument_columns, drop = FALSE]
  statistic <- as.numeric(t(beta) %*% solve(covariance, beta)) / length(beta)
  if (length(statistic) != 1 || !is.finite(statistic)) stop("first-stage F is unavailable")
  data.frame(
    endogenous = endogenous_name,
    instruments = paste(instrument_columns, collapse = ";"),
    f_statistic = statistic, threshold = weak_threshold
  )
}))
utils::write.csv(first_stage, file.path(output_root, "first_stage.csv"), row.names = FALSE)

overidentification <- data.frame(
  test = character(), statistic = numeric(), p_value = numeric(),
  degrees_of_freedom = integer()
)
if (length(instrument_columns) > length(endogenous_columns)) {
  sargan <- fixest::fitstat(model, "sargan")$sargan
  if (is.null(sargan) || any(!is.finite(c(sargan$stat, sargan$p, sargan$df)))) {
    stop("overidentification evidence is unavailable")
  }
  overidentification <- data.frame(
    test = "Sargan", statistic = sargan$stat, p_value = sargan$p,
    degrees_of_freedom = as.integer(sargan$df)
  )
}
utils::write.csv(
  overidentification, file.path(output_root, "overidentification.csv"), row.names = FALSE
)

reduced_formula <- stats::as.formula(sprintf(
  "%s ~ %s | %s", quote_identifier(outcome_column),
  join_terms(c(instrument_columns, control_columns)), fixed_effects
))
reduced_model <- fixest::feols(reduced_formula, data = data, vcov = vcov_value)
reduced <- normalize(
  as.data.frame(fixest::coeftable(reduced_model)), instrument_columns
)
utils::write.csv(reduced, file.path(output_root, "reduced_form.csv"), row.names = FALSE)

clusters <- if (is.null(cluster_column)) NA_integer_ else length(unique(data[[cluster_column]]))
utils::write.csv(
  data.frame(observations = nrow(data), clusters = clusters),
  file.path(output_root, "support.csv"), row.names = FALSE, na = ""
)
axis_min <- min(c(structural$conf_low, 0))
axis_max <- max(c(structural$conf_high, 0))
axis_padding <- max((axis_max - axis_min) * 0.08, .Machine$double.eps)
axis_min <- axis_min - axis_padding
axis_max <- axis_max + axis_padding
map_x <- function(value) 160 + (value - axis_min) / (axis_max - axis_min) * 510
x_ticks <- seq(axis_min, axis_max, length.out = 5)
row_y <- function(index) 45 + index * 34
plot_height <- max(300, 150 + nrow(structural) * 34)
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
  vapply(seq_len(nrow(structural)), function(index) sprintf(
    '<g><text x="150" y="%.3f" text-anchor="end" font-family="sans-serif" font-size="12">%s</text><line class="confidence-interval" x1="%.3f" y1="%.3f" x2="%.3f" y2="%.3f" stroke="#2369a8" stroke-width="2"/><circle class="estimate" cx="%.3f" cy="%.3f" r="4" fill="#2369a8"/></g>',
    row_y(index) + 4, xml_escape(structural$term[index]),
    map_x(structural$conf_low[index]), row_y(index),
    map_x(structural$conf_high[index]), row_y(index),
    map_x(structural$estimate[index]), row_y(index)
  ), character(1)),
  sprintf('<text x="415" y="%.3f" text-anchor="middle" font-family="sans-serif" font-size="13">2SLS estimate with confidence interval</text>', plot_height - 8),
  '</svg>'
)
writeLines(svg, file.path(output_root, "coefficient_plot.svg"), useBytes = TRUE)
configuration <- data.frame(
  method_id = "iv-2sls", r_version = R.version.string,
  fixest_version = as.character(utils::packageVersion("fixest")),
  confidence_level = confidence_level,
  cluster_column = if (is.null(cluster_column)) "" else cluster_column,
  fixed_effects = paste(fixed_effect_columns, collapse = ";"),
  estimator_label = "fixest::feols-2sls",
  cutoff = "", bandwidth = "", kernel = "", donut_radius = ""
)
utils::write.csv(
  configuration, file.path(output_root, "package_configuration.csv"), row.names = FALSE
)
