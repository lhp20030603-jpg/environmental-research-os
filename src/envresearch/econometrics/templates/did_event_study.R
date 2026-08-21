options(warn = 2)
set.seed(__SEED__)

unit_column <- __UNIT__
time_column <- __TIME__
outcome_column <- __OUTCOME__
cohort_column <- __COHORT__
covariate_columns <- __COVARIATES__
comparison_group <- __COMPARISON__
reference_period <- __REFERENCE__
confidence_level <- __CONFIDENCE__
interval_mode <- __INTERVAL_MODE__
cluster_column <- __CLUSTER__

input_path <- "input/data.csv"
output_root <- "output"
dir.create(output_root, showWarnings = FALSE, recursive = FALSE)
data <- utils::read.csv(input_path, check.names = FALSE, stringsAsFactors = FALSE)
required <- c(unit_column, time_column, outcome_column, cohort_column, covariate_columns)
if (!all(required %in% names(data))) stop("declared columns are missing")
required_noncohort <- setdiff(required, cohort_column)
complete <- stats::complete.cases(data[, required_noncohort, drop = FALSE])
if (!all(complete)) stop("undeclared observation removal would be required")
if (anyDuplicated(data[, c(unit_column, time_column), drop = FALSE])) stop("duplicate panel keys")

data$treated_ever_internal <- !is.na(data[[cohort_column]])
data$event_time_internal <- ifelse(
  data$treated_ever_internal,
  data[[time_column]] - data[[cohort_column]],
  reference_period
)
quote_identifier <- function(value) paste0("`", value, "`")
rhs <- if (length(covariate_columns)) {
  paste(vapply(covariate_columns, quote_identifier, character(1)), collapse = " + ")
} else {
  "1"
}
formula_text <- sprintf(
  "%s ~ fixest::i(event_time_internal, treated_ever_internal, ref = %d) + %s | %s + %s",
  quote_identifier(outcome_column), reference_period, rhs,
  quote_identifier(unit_column), quote_identifier(time_column)
)
baseline_model <- fixest::feols(
  stats::as.formula(formula_text), data = data, cluster = data[[cluster_column]]
)
baseline_table <- as.data.frame(fixest::coeftable(baseline_model))

control_group <- if (comparison_group == "never-treated") "nevertreated" else "notyettreated"
did_data <- data
did_data[[cohort_column]] <- ifelse(is.na(did_data[[cohort_column]]), 0, did_data[[cohort_column]])
did_id_column <- ".envresearch_did_id"
while (did_id_column %in% names(did_data)) did_id_column <- paste0(did_id_column, "_")
did_data[[did_id_column]] <- match(did_data[[unit_column]], unique(did_data[[unit_column]]))
did_cluster_column <- if (cluster_column == unit_column) did_id_column else cluster_column
small_group_warning <- function(condition) {
  if (grepl(
    "some small groups in your dataset", conditionMessage(condition), fixed = TRUE
  )) {
    message("Warning: ", conditionMessage(condition))
    invokeRestart("muffleWarning")
  }
}
group_time_model <- withCallingHandlers(
  did::att_gt(
    yname = outcome_column,
    tname = time_column,
    idname = did_id_column,
    gname = cohort_column,
    xformla = stats::as.formula(paste("~", rhs)),
    data = did_data,
    control_group = control_group,
    base_period = "varying",
    anticipation = 0,
    clustervars = did_cluster_column,
    bstrap = TRUE,
    cband = interval_mode == "simultaneous",
    alp = 1 - confidence_level
  ),
  warning = small_group_warning
)
dynamic_model <- did::aggte(
  group_time_model, type = "dynamic", bstrap = TRUE,
  cband = interval_mode == "simultaneous",
  alp = 1 - confidence_level
)

pointwise_critical <- stats::qnorm(1 - (1 - confidence_level) / 2)
group_critical <- if (interval_mode == "simultaneous") group_time_model$c else pointwise_critical
dynamic_critical <- if (interval_mode == "simultaneous") dynamic_model$crit.val.egt else pointwise_critical
if (is.null(group_critical) || is.null(dynamic_critical)) stop("requested confidence band is unavailable")
normalize <- function(term, event_time, group, time, estimate, std_error, critical) {
  data.frame(
    term = term, event_time = event_time, group = group, time = time,
    estimate = estimate, std_error = std_error,
    conf_low = estimate - critical * std_error,
    conf_high = estimate + critical * std_error, check.names = FALSE
  )
}
baseline_terms <- row.names(baseline_table)
baseline_keep <- grepl("event_time_internal", baseline_terms, fixed = TRUE)
baseline_table <- baseline_table[baseline_keep, , drop = FALSE]
baseline_terms <- baseline_terms[baseline_keep]
if (!nrow(baseline_table)) stop("baseline event-study estimates are unavailable")
baseline_critical <- if (interval_mode == "simultaneous") {
  stats::qnorm(1 - (1 - confidence_level) / (2 * nrow(baseline_table)))
} else {
  pointwise_critical
}
baseline_event <- as.integer(sub(":.*$", "", sub(".*::", "", baseline_terms)))
baseline <- normalize(
  baseline_terms, baseline_event, NA_integer_, NA_integer_,
  baseline_table[[1]], baseline_table[[2]], baseline_critical
)
group_time <- normalize(
  paste0("att_", group_time_model$group, "_", group_time_model$t),
  group_time_model$t - group_time_model$group,
  as.integer(group_time_model$group), as.integer(group_time_model$t),
  group_time_model$att, group_time_model$se, group_critical
)
dynamic <- normalize(
  paste0("event_time_", dynamic_model$egt), as.integer(dynamic_model$egt),
  NA_integer_, NA_integer_, dynamic_model$att.egt, dynamic_model$se.egt, dynamic_critical
)
utils::write.csv(baseline, file.path(output_root, "baseline.csv"), row.names = FALSE, na = "")
utils::write.csv(group_time, file.path(output_root, "group_time_att.csv"), row.names = FALSE, na = "")
utils::write.csv(dynamic, file.path(output_root, "dynamic.csv"), row.names = FALSE, na = "")

treated_units <- unique(data[[unit_column]][!is.na(data[[cohort_column]])])
comparison_units <- if (comparison_group == "never-treated") {
  unique(data[[unit_column]][is.na(data[[cohort_column]])])
} else {
  unique(data[[unit_column]][
    is.na(data[[cohort_column]]) | data[[time_column]] < data[[cohort_column]]
  ])
}
support <- data.frame(
  observations = nrow(data), units = length(unique(data[[unit_column]])),
  treated_units = length(treated_units), comparison_units = length(comparison_units),
  cohorts = length(unique(stats::na.omit(data[[cohort_column]]))),
  dropped_observations = 0,
  duplicate_panel_keys = 0,
  removal_rule = "complete-declared-columns"
)
utils::write.csv(support, file.path(output_root, "support.csv"), row.names = FALSE)

support_cells <- do.call(rbind, lapply(seq_along(group_time_model$group), function(index) {
  group_value <- group_time_model$group[index]
  time_value <- group_time_model$t[index]
  treated_mask <- !is.na(data[[cohort_column]]) &
    data[[cohort_column]] == group_value & data[[time_column]] == time_value
  comparison_mask <- data[[time_column]] == time_value & if (comparison_group == "never-treated") {
    is.na(data[[cohort_column]])
  } else {
    is.na(data[[cohort_column]]) | data[[cohort_column]] > time_value
  }
  data.frame(
    group = group_value, time = time_value, event_time = time_value - group_value,
    treated_observations = sum(treated_mask), comparison_observations = sum(comparison_mask),
    treated_units = length(unique(data[[unit_column]][treated_mask])),
    comparison_units = length(unique(data[[unit_column]][comparison_mask]))
  )
}))
utils::write.csv(support_cells, file.path(output_root, "support_by_group_time.csv"), row.names = FALSE)

cohorts <- sort(unique(stats::na.omit(data[[cohort_column]])))
cohort_timing <- do.call(rbind, lapply(cohorts, function(cohort_value) {
  cohort_mask <- !is.na(data[[cohort_column]]) &
    data[[cohort_column]] == cohort_value
  cohort_units <- unique(data[[unit_column]][cohort_mask])
  cohort_rows <- data[[unit_column]] %in% cohort_units
  data.frame(
    cohort = cohort_value, units = length(cohort_units),
    first_period = min(data[[time_column]][cohort_rows]),
    last_period = max(data[[time_column]][cohort_rows])
  )
}))
utils::write.csv(cohort_timing, file.path(output_root, "cohort_timing.csv"), row.names = FALSE)

comparison_mask_all <- if (comparison_group == "never-treated") {
  is.na(data[[cohort_column]])
} else {
  is.na(data[[cohort_column]]) | data[[time_column]] < data[[cohort_column]]
}
balance <- data.frame(
  covariate = character(), treated_mean = numeric(), comparison_mean = numeric(),
  standardized_difference = numeric(), treated_n = integer(), comparison_n = integer()
)
for (covariate in covariate_columns) {
  treated_values <- data[[covariate]][data$treated_ever_internal]
  comparison_values <- data[[covariate]][comparison_mask_all]
  pooled_sd <- sqrt((stats::var(treated_values) + stats::var(comparison_values)) / 2)
  treated_mean <- mean(treated_values)
  comparison_mean <- mean(comparison_values)
  standardized <- if (is.finite(pooled_sd) && pooled_sd > 0) {
    (treated_mean - comparison_mean) / pooled_sd
  } else if (isTRUE(all.equal(treated_mean, comparison_mean))) {
    0
  } else {
    stop("degenerate covariate overlap")
  }
  balance <- rbind(balance, data.frame(
    covariate = covariate, treated_mean = treated_mean,
    comparison_mean = comparison_mean, standardized_difference = standardized,
    treated_n = length(treated_values), comparison_n = length(comparison_values)
  ))
}
utils::write.csv(balance, file.path(output_root, "covariate_balance.csv"), row.names = FALSE)

if (!all(is.finite(unlist(dynamic[, c("event_time", "estimate", "conf_low", "conf_high")])))) {
  stop("event-study plot values are not finite")
}
svg_width <- 720
svg_height <- 480
svg_margin <- 60
x_limits <- range(dynamic$event_time)
y_limits <- range(c(dynamic$conf_low, dynamic$conf_high, 0))
expand_limits <- function(limits) {
  if (limits[1] == limits[2]) limits + c(-0.5, 0.5) else limits
}
x_limits <- expand_limits(x_limits)
y_limits <- expand_limits(y_limits)
scale_x <- function(value) {
  svg_margin + (value - x_limits[1]) / diff(x_limits) * (svg_width - 2 * svg_margin)
}
scale_y <- function(value) {
  svg_height - svg_margin - (value - y_limits[1]) / diff(y_limits) * (svg_height - 2 * svg_margin)
}
x_values <- scale_x(dynamic$event_time)
y_values <- scale_y(dynamic$estimate)
treatment_boundary <- -0.5
x_ticks <- sort(unique(dynamic$event_time))
y_ticks <- pretty(y_limits, n = 5)
y_ticks <- y_ticks[y_ticks >= y_limits[1] & y_ticks <= y_limits[2]]
if (length(y_ticks) < 2) y_ticks <- y_limits
svg_lines <- c(
  '<?xml version="1.0" encoding="UTF-8"?>',
  sprintf('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d">', svg_width, svg_height, svg_width, svg_height),
  '<rect width="100%" height="100%" fill="white"/>',
  sprintf('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="black"/>', svg_margin, svg_height - svg_margin, svg_width - svg_margin, svg_height - svg_margin),
  sprintf('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="black"/>', svg_margin, svg_margin, svg_margin, svg_height - svg_margin),
  sprintf('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#777" stroke-dasharray="6,4"/>', svg_margin, scale_y(0), svg_width - svg_margin, scale_y(0)),
  sprintf('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#999" stroke-dasharray="3,4"/>', scale_x(treatment_boundary), svg_margin, scale_x(treatment_boundary), svg_height - svg_margin),
  vapply(x_ticks, function(value) sprintf(
    '<g class="x-tick"><line x1="%g" y1="%g" x2="%g" y2="%g" stroke="black"/><text x="%g" y="%g" text-anchor="middle" font-family="sans-serif" font-size="12">%s</text></g>',
    scale_x(value), svg_height - svg_margin, scale_x(value), svg_height - svg_margin + 6,
    scale_x(value), svg_height - svg_margin + 22, format(value, trim = TRUE)
  ), character(1)),
  vapply(y_ticks, function(value) sprintf(
    '<g class="y-tick"><line x1="%g" y1="%g" x2="%g" y2="%g" stroke="black"/><text x="%g" y="%g" text-anchor="end" dominant-baseline="middle" font-family="sans-serif" font-size="12">%s</text></g>',
    svg_margin - 6, scale_y(value), svg_margin, scale_y(value),
    svg_margin - 10, scale_y(value), format(value, digits = 4, trim = TRUE)
  ), character(1)),
  sprintf('<polyline points="%s" fill="none" stroke="#1f5a94" stroke-width="2"/>', paste(sprintf("%g,%g", x_values, y_values), collapse = " ")),
  vapply(seq_along(x_values), function(index) sprintf(
    '<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#1f5a94"/>',
    x_values[index], scale_y(dynamic$conf_low[index]), x_values[index], scale_y(dynamic$conf_high[index])
  ), character(1)),
  sprintf('<circle cx="%g" cy="%g" r="4" fill="#1f5a94"/>', x_values, y_values),
  sprintf('<text x="%g" y="%g" text-anchor="middle" font-family="sans-serif" font-size="14">Event time</text>', svg_width / 2, svg_height - 15),
  sprintf('<text x="18" y="%g" text-anchor="middle" font-family="sans-serif" font-size="14" transform="rotate(-90 18 %g)">ATT</text>', svg_height / 2, svg_height / 2),
  '</svg>'
)
writeLines(svg_lines, file.path(output_root, "event_study.svg"), useBytes = TRUE)

package_configuration <- data.frame(
  r_version = R.version.string,
  fixest_version = as.character(utils::packageVersion("fixest")),
  did_version = as.character(utils::packageVersion("did")),
  bootstrap_seed = __SEED__, comparison_group = comparison_group,
  reference_period = reference_period, base_period = "varying", anticipation = 0,
  confidence_level = confidence_level, interval_mode = interval_mode,
  baseline_interval_method = if (interval_mode == "simultaneous") "bonferroni-normal" else "pointwise-normal",
  did_interval_method = if (interval_mode == "simultaneous") "multiplier-bootstrap" else "pointwise-normal",
  cluster_column = cluster_column
)
utils::write.csv(package_configuration, file.path(output_root, "package_configuration.csv"), row.names = FALSE)
