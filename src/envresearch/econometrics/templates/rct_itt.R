options(warn = 2)

unit_column <- __UNIT__
assignment_column <- __ASSIGNMENT__
outcome_column <- __OUTCOME__
baseline_columns <- __BASELINES__
confidence_level <- __CONFIDENCE__
max_attrition_rate <- __MAX_ATTRITION__
balance_smd_threshold <- __BALANCE_THRESHOLD__

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
assignment <- data[[assignment_column]]
outcome <- data[[outcome_column]]
if (!identical(sort(unique(assignment)), c(0L, 1L))) stop("RCT requires exact binary arms")
if (anyDuplicated(data[[unit_column]])) stop("RCT units must be unique")
complete <- !is.na(outcome)
if (any(vapply(c(0L, 1L), function(arm) sum(complete & assignment == arm) == 0, logical(1)))) {
  stop("RCT arm has no observed outcomes")
}

critical <- stats::qnorm(1 - (1 - confidence_level) / 2)
coefficient_frame <- function(model) {
  table <- as.data.frame(fixest::coeftable(model))
  term <- assignment_column
  if (!(term %in% row.names(table))) stop("RCT treatment estimate is unavailable")
  estimate <- table[term, 1]
  std_error <- table[term, 2]
  data.frame(
    term = term, estimate = estimate, std_error = std_error,
    conf_low = estimate - critical * std_error,
    conf_high = estimate + critical * std_error, check.names = FALSE
  )
}
unadjusted_formula <- stats::as.formula(sprintf(
  "%s ~ %s", quote_identifier(outcome_column), quote_identifier(assignment_column)
))
ancova_formula <- stats::as.formula(sprintf(
  "%s ~ %s + %s", quote_identifier(outcome_column), quote_identifier(assignment_column),
  paste(vapply(baseline_columns, quote_identifier, character(1)), collapse = " + ")
))
unadjusted_model <- fixest::feols(unadjusted_formula, data = data, vcov = "hetero")
ancova_model <- fixest::feols(ancova_formula, data = data, vcov = "hetero")
unadjusted <- coefficient_frame(unadjusted_model)
ancova <- coefficient_frame(ancova_model)
utils::write.csv(unadjusted, file.path(output_root, "unadjusted.csv"), row.names = FALSE)
utils::write.csv(ancova, file.path(output_root, "ancova.csv"), row.names = FALSE)

allocation <- do.call(rbind, lapply(c(0L, 1L), function(arm) {
  selected <- assignment == arm
  data.frame(
    arm = if (arm == 0L) "control" else "treated",
    assigned = sum(selected), outcomes_observed = sum(selected & complete),
    outcomes_missing = sum(selected & !complete)
  )
}))
utils::write.csv(allocation, file.path(output_root, "allocation.csv"), row.names = FALSE)
attrition <- data.frame(
  attrition_rate = sum(!complete) / nrow(data),
  max_attrition_rate = max_attrition_rate
)
utils::write.csv(attrition, file.path(output_root, "attrition.csv"), row.names = FALSE)

balance <- do.call(rbind, lapply(baseline_columns, function(column) {
  control <- data[[column]][assignment == 0L]
  treated <- data[[column]][assignment == 1L]
  pooled <- sqrt((stats::var(control) + stats::var(treated)) / 2)
  difference <- mean(treated) - mean(control)
  smd <- if (pooled == 0 && difference == 0) 0 else difference / pooled
  data.frame(term = column, smd = smd, check.names = FALSE)
}))
utils::write.csv(balance, file.path(output_root, "balance.csv"), row.names = FALSE)

axis_min <- min(c(unadjusted$conf_low, ancova$conf_low, 0))
axis_max <- max(c(unadjusted$conf_high, ancova$conf_high, 0))
padding <- max((axis_max - axis_min) * 0.08, .Machine$double.eps)
axis_min <- axis_min - padding
axis_max <- axis_max + padding
map_x <- function(value) 160 + (value - axis_min) / (axis_max - axis_min) * 510
x_ticks <- seq(axis_min, axis_max, length.out = 5)
plot_rows <- rbind(
  data.frame(label = "Unadjusted", unadjusted),
  data.frame(label = "ANCOVA", ancova)
)
row_y <- function(index) 45 + index * 44
axis_y <- 220
svg <- c(
  '<?xml version="1.0" encoding="UTF-8"?>',
  '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="270" viewBox="0 0 720 270">',
  '<rect width="100%" height="100%" fill="white"/>',
  '<line x1="160" y1="220" x2="670" y2="220" stroke="#333"/>',
  sprintf('<line class="zero-line" x1="%.3f" y1="25" x2="%.3f" y2="220" stroke="#777" stroke-dasharray="5,4"/>', map_x(0), map_x(0)),
  vapply(x_ticks, function(value) sprintf(
    '<g class="x-tick"><line x1="%.3f" y1="220" x2="%.3f" y2="226" stroke="#333"/><text x="%.3f" y="244" text-anchor="middle" font-family="sans-serif" font-size="11">%.3g</text></g>',
    map_x(value), map_x(value), map_x(value), value
  ), character(1)),
  vapply(seq_len(nrow(plot_rows)), function(index) sprintf(
    '<g><text x="150" y="%.3f" text-anchor="end" font-family="sans-serif" font-size="12">%s</text><line class="confidence-interval" x1="%.3f" y1="%.3f" x2="%.3f" y2="%.3f" stroke="#2369a8" stroke-width="2"/><circle class="estimate" cx="%.3f" cy="%.3f" r="4" fill="#2369a8"/></g>',
    row_y(index) + 4, xml_escape(plot_rows$label[index]),
    map_x(plot_rows$conf_low[index]), row_y(index),
    map_x(plot_rows$conf_high[index]), row_y(index),
    map_x(plot_rows$estimate[index]), row_y(index)
  ), character(1)),
  '</svg>'
)
writeLines(svg, file.path(output_root, "coefficient_plot.svg"), useBytes = TRUE)

configuration <- data.frame(
  method_id = "rct-itt", r_version = R.version.string,
  confidence_level = confidence_level,
  balance_smd_threshold = balance_smd_threshold
)
utils::write.csv(
  configuration, file.path(output_root, "package_configuration.csv"), row.names = FALSE
)
