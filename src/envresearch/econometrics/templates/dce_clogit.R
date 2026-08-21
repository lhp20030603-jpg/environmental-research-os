options(warn = 2)

respondent_column <- __RESPONDENT__; choice_set_column <- __CHOICE_SET__
alternative_column <- __ALTERNATIVE__; chosen_column <- __CHOSEN__; cost_column <- __COST__
attribute_columns <- __ATTRIBUTES__; cluster_column <- __CLUSTER__
confidence_level <- __CONFIDENCE__; min_abs_cost <- __MIN_COST__
max_sensitivity_change <- __MAX_SENSITIVITY__
currency <- __CURRENCY__; price_base <- __PRICE_BASE__
time_basis <- __TIME_BASIS__; population_basis <- __POPULATION_BASIS__

input_path <- "input/data.csv"; output_root <- "output"
dir.create(output_root, showWarnings = FALSE, recursive = FALSE)
managed_library <- normalizePath(Sys.getenv("R_LIBS_USER"), mustWork = TRUE)
invisible(loadNamespace("survival", lib.loc = managed_library))
strata <- survival::strata; cluster <- survival::cluster; coxph <- survival::coxph
audit_managed_namespaces <- function() {
  base_packages <- c("base", "compiler", "datasets", "graphics", "grDevices", "grid", "methods", "parallel", "splines", "stats", "stats4", "tcltk", "tools", "utils")
  for (package in loadedNamespaces()) {
    if (package %in% base_packages) next
    namespace <- asNamespace(package, base.OK = TRUE)
    package_path <- getNamespaceInfo(namespace, "path")
    if (is.null(package_path)) next
    if (normalizePath(dirname(package_path), mustWork = TRUE) != managed_library) stop("R package escaped managed authority")
  }
}
data <- utils::read.csv(input_path, check.names = FALSE, stringsAsFactors = FALSE)
data[[".choice_stratum"]] <- interaction(data[[respondent_column]], data[[choice_set_column]], drop = TRUE)
quote_id <- function(value) paste0("`", value, "`")
terms <- c(cost_column, attribute_columns)
rhs <- paste(vapply(terms, quote_id, character(1)), collapse = " + ")
formula <- stats::as.formula(sprintf("%s ~ %s + strata(.choice_stratum) + cluster(%s)", quote_id(chosen_column), rhs, quote_id(cluster_column)))
model <- survival::clogit(formula, data = data, method = "efron")
sensitivity_formula <- stats::as.formula(sprintf("%s ~ %s + factor(%s) + strata(.choice_stratum) + cluster(%s)", quote_id(chosen_column), rhs, quote_id(alternative_column), quote_id(cluster_column)))
sensitivity_model <- survival::clogit(sensitivity_formula, data = data, method = "efron")
audit_managed_namespaces()
emit_failure <- function(code, status) {
  cat(paste0("ENVRESEARCH_CODE:", code, "\n"), file = stderr())
  quit(save = "no", status = status, runLast = FALSE)
}
table <- as.data.frame(summary(model)$coefficients); emitted_terms <- row.names(table)
if (!all(terms %in% emitted_terms)) emit_failure("DCE_TERM_UNIDENTIFIED", 51)
cost_beta <- stats::coef(model)[[cost_column]]
if (!is.finite(cost_beta) || cost_beta >= -min_abs_cost) emit_failure("DCE_COST_SLOPE_INVALID", 52)
critical <- stats::qnorm(1 - (1 - confidence_level) / 2)
estimates <- stats::coef(model)[terms]; covariance <- stats::vcov(model)[terms, terms, drop = FALSE]
standard_errors <- sqrt(diag(covariance))
utils::write.csv(data.frame(term = terms, estimate = estimates, std_error = standard_errors, confidence_low = estimates - critical * standard_errors, confidence_high = estimates + critical * standard_errors), file.path(output_root, "coefficients.csv"), row.names = FALSE)
covariance_rows <- expand.grid(row_term = terms, column_term = terms, stringsAsFactors = FALSE)
covariance_rows$value <- mapply(function(left, right) covariance[left, right], covariance_rows$row_term, covariance_rows$column_term)
utils::write.csv(covariance_rows, file.path(output_root, "covariance.csv"), row.names = FALSE)

wtp_rows <- lapply(attribute_columns, function(attribute) {
  beta <- estimates[[attribute]]; estimate <- -beta / cost_beta
  gradient <- c(beta / cost_beta ^ 2, -1 / cost_beta)
  selected <- covariance[c(cost_column, attribute), c(cost_column, attribute), drop = FALSE]
  standard_error <- sqrt(as.numeric(t(gradient) %*% selected %*% gradient))
  data.frame(name = paste0(attribute, "-wtp"), estimate = estimate, std_error = standard_error, confidence_low = estimate - critical * standard_error, confidence_high = estimate + critical * standard_error, currency = currency, price_base = price_base, time_basis = time_basis, population_basis = population_basis, transformation = "negative-attribute-over-cost", numerator_term = attribute, denominator_term = cost_column)
})
utils::write.csv(do.call(rbind, wtp_rows), file.path(output_root, "wtp.csv"), row.names = FALSE)
utils::write.csv(data.frame(observations = nrow(data), primary_units = length(unique(data[[respondent_column]])), groups = length(unique(data[[".choice_stratum"]])), zero_or_no_count = sum(data[[chosen_column]] == 0), min_abs_cost_coefficient = min_abs_cost), file.path(output_root, "choice_support.csv"), row.names = FALSE)

sensitivity_cost <- stats::coef(sensitivity_model)[[cost_column]]
baseline_wtp <- -estimates[[attribute_columns[[1]]]] / cost_beta
sensitivity_attribute <- stats::coef(sensitivity_model)[[attribute_columns[[1]]]]
sensitivity_wtp <- -sensitivity_attribute / sensitivity_cost
utils::write.csv(data.frame(label = "include-alternative-specific-constants", estimate = sensitivity_wtp, baseline_estimate = baseline_wtp, absolute_change = abs(sensitivity_wtp - baseline_wtp), max_sensitivity_change = max_sensitivity_change, numerator_coefficient = sensitivity_attribute, denominator_coefficient = sensitivity_cost, model_form = "conditional-logit"), file.path(output_root, "sensitivity.csv"), row.names = FALSE)
utils::write.csv(data.frame(method_id = "dce-clogit", r_version = R.version.string, confidence_level = confidence_level, cluster_column = cluster_column, fixed_effects = "", functional_form = "", family = "", link = ""), file.path(output_root, "package_configuration.csv"), row.names = FALSE)

values <- vapply(wtp_rows, function(row) row$estimate, numeric(1)); lows <- vapply(wtp_rows, function(row) row$confidence_low, numeric(1)); highs <- vapply(wtp_rows, function(row) row$confidence_high, numeric(1)); axis_min <- min(lows, 0); axis_max <- max(highs, 0)
padding <- max(axis_max - axis_min, max(abs(c(axis_min, axis_max)), 1) * 1e-6) * 0.08; axis_min <- axis_min - padding; axis_max <- axis_max + padding
map_x <- function(value) 120 + (value - axis_min) / (axis_max - axis_min) * 450; ticks <- seq(axis_min, axis_max, length.out = 5); axis_y <- 60 + 30 * length(wtp_rows); height <- axis_y + 50
xml_escape <- function(value) { value <- gsub("&", "&amp;", value, fixed = TRUE); value <- gsub("<", "&lt;", value, fixed = TRUE); value <- gsub(">", "&gt;", value, fixed = TRUE); value <- gsub('"', "&quot;", value, fixed = TRUE); gsub("'", "&apos;", value, fixed = TRUE) }
tick_svg <- paste(vapply(ticks, function(value) sprintf('<g class="x-tick"><line x1="%.3f" y1="%d" x2="%.3f" y2="%d"/><text x="%.3f" y="%d">%.3g</text></g>', map_x(value), axis_y, map_x(value), axis_y + 6, map_x(value), axis_y + 22, value), character(1)), collapse = "")
estimate_svg <- paste(vapply(seq_along(wtp_rows), function(index) { row <- wtp_rows[[index]]; y <- 35 + 30 * index; sprintf('<g class="wtp-estimate"><text x="5" y="%d">%s</text><line x1="%.3f" y1="%d" x2="%.3f" y2="%d" stroke="#2369a8"/><circle cx="%.3f" cy="%d" r="4"/></g>', y + 4, xml_escape(attribute_columns[[index]]), map_x(row$confidence_low), y, map_x(row$confidence_high), y, map_x(row$estimate), y) }, character(1)), collapse = "")
svg <- sprintf('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="%d" viewBox="0 0 640 %d"><line x1="120" y1="%d" x2="570" y2="%d"/>%s%s</svg>', height, height, axis_y, axis_y, tick_svg, estimate_svg)
writeLines(svg, file.path(output_root, "dce_plot.svg"), useBytes = TRUE)
