options(warn = 2)

price_column <- __PRICE__
environment_column <- __ENVIRONMENT__
control_columns <- __CONTROLS__
fixed_effect_columns <- __FIXED_EFFECTS__
cluster_column <- __CLUSTER__
functional_form <- __FORM__
sensitivity_form <- __SENSITIVITY_FORM__
confidence_level <- __CONFIDENCE__
max_condition_number <- __MAX_CONDITION__
max_sensitivity_change <- __MAX_SENSITIVITY__
currency <- __CURRENCY__; price_base <- __PRICE_BASE__
time_basis <- __TIME_BASIS__; population_basis <- __POPULATION_BASIS__

input_path <- "input/data.csv"; output_root <- "output"
dir.create(output_root, showWarnings = FALSE, recursive = FALSE)
managed_library <- normalizePath(Sys.getenv("R_LIBS_USER"), mustWork = TRUE)
invisible(loadNamespace("fixest", lib.loc = managed_library))
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
quote_id <- function(value) paste0("`", value, "`")
transformed <- function(form) {
  frame <- data
  frame[[".valuation_y"]] <- if (startsWith(form, "log-")) log(frame[[price_column]]) else frame[[price_column]]
  frame[[".valuation_x"]] <- if (endsWith(form, "-log")) log(frame[[environment_column]]) else frame[[environment_column]]
  frame
}
fit <- function(form) {
  frame <- transformed(form)
  rhs <- paste(c(".valuation_x", vapply(control_columns, quote_id, character(1))), collapse = " + ")
  fe <- if (length(fixed_effect_columns)) paste(vapply(fixed_effect_columns, quote_id, character(1)), collapse = " + ") else "0"
  formula <- stats::as.formula(sprintf(".valuation_y ~ %s | %s", rhs, fe))
  vcov_value <- if (is.null(cluster_column)) "hetero" else stats::as.formula(paste("~", quote_id(cluster_column)))
  fixest::feols(formula, data = frame, vcov = vcov_value)
}
model <- fit(functional_form); sensitivity_model <- fit(sensitivity_form)
audit_managed_namespaces()
table <- as.data.frame(fixest::coeftable(model)); terms <- row.names(table)
index <- match(".valuation_x", terms)
if (is.na(index)) {
  cat("ENVRESEARCH_CODE:HEDONIC_TERM_UNIDENTIFIED\n", file = stderr())
  quit(save = "no", status = 31, runLast = FALSE)
}
critical <- stats::qnorm(1 - (1 - confidence_level) / 2)
beta <- table[index, 1]; beta_se <- table[index, 2]
coefficients <- data.frame(
  term = environment_column, estimate = beta, std_error = beta_se,
  confidence_low = beta - critical * beta_se,
  confidence_high = beta + critical * beta_se
)
utils::write.csv(coefficients, file.path(output_root, "coefficients.csv"), row.names = FALSE)
utils::write.csv(data.frame(row_term = environment_column, column_term = environment_column, value = beta_se ^ 2), file.path(output_root, "covariance.csv"), row.names = FALSE)

reference_price <- mean(data[[price_column]]); reference_environment <- mean(data[[environment_column]])
implicit_multiplier <- function(form) switch(form, "level-level" = 1, "log-level" = reference_price, "level-log" = 1 / reference_environment, "log-log" = reference_price / reference_environment)
multiplier <- implicit_multiplier(functional_form)
implicit_price <- beta * multiplier; implicit_se <- beta_se * abs(multiplier)
utils::write.csv(data.frame(
  name = "implicit-price", estimate = implicit_price, std_error = implicit_se,
  confidence_low = implicit_price - critical * implicit_se,
  confidence_high = implicit_price + critical * implicit_se,
  currency = currency, price_base = price_base, time_basis = time_basis,
  population_basis = population_basis, transformation = "marginal-implicit-price",
  numerator_term = environment_column, denominator_term = price_column
), file.path(output_root, "implicit_price.csv"), row.names = FALSE)

groups <- if (!length(fixed_effect_columns)) NA_integer_ else nrow(unique(data[fixed_effect_columns]))
utils::write.csv(data.frame(observations = nrow(data), primary_units = nrow(data), groups = groups, zero_or_no_count = 0), file.path(output_root, "support.csv"), row.names = FALSE, na = "")
condition_number <- kappa(stats::model.matrix(model), exact = TRUE)
design_matrix <- stats::model.matrix(model)
vif_matrix <- design_matrix[, apply(design_matrix, 2, stats::sd) > 0, drop = FALSE]
max_vif <- if (ncol(vif_matrix) < 2) 1 else max(diag(solve(stats::cor(vif_matrix))))
utils::write.csv(data.frame(condition_number = condition_number, max_condition_number = max_condition_number, max_vif = max_vif, reference_price = reference_price, reference_environment = reference_environment), file.path(output_root, "collinearity.csv"), row.names = FALSE)
sensitivity_beta <- stats::coef(sensitivity_model)[[".valuation_x"]]
sensitivity_implicit_price <- sensitivity_beta * implicit_multiplier(sensitivity_form)
utils::write.csv(data.frame(label = "alternative-functional-form", estimate = sensitivity_implicit_price, baseline_estimate = implicit_price, absolute_change = abs(sensitivity_implicit_price - implicit_price), max_sensitivity_change = max_sensitivity_change, raw_coefficient = sensitivity_beta, model_form = sensitivity_form), file.path(output_root, "sensitivity.csv"), row.names = FALSE)
utils::write.csv(data.frame(method_id = "hedonic-pricing", r_version = R.version.string, confidence_level = confidence_level, cluster_column = if (is.null(cluster_column)) "" else cluster_column, fixed_effects = paste(fixed_effect_columns, collapse = ";"), functional_form = functional_form, family = "", link = ""), file.path(output_root, "package_configuration.csv"), row.names = FALSE)

axis_min <- min(coefficients$confidence_low, 0); axis_max <- max(coefficients$confidence_high, 0)
padding <- max(axis_max - axis_min, max(abs(c(axis_min, axis_max)), 1) * 1e-6) * 0.08
axis_min <- axis_min - padding; axis_max <- axis_max + padding
map_x <- function(value) 90 + (value - axis_min) / (axis_max - axis_min) * 480
ticks <- seq(axis_min, axis_max, length.out = 5)
tick_svg <- paste(vapply(ticks, function(value) sprintf('<g class="x-tick"><line x1="%.3f" y1="120" x2="%.3f" y2="126"/><text x="%.3f" y="142">%.3g</text></g>', map_x(value), map_x(value), map_x(value), value), character(1)), collapse = "")
svg <- sprintf('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="170" viewBox="0 0 640 170"><line x1="90" y1="120" x2="570" y2="120"/>%s<line x1="%.3f" y1="65" x2="%.3f" y2="65" stroke="#2369a8"/><circle cx="%.3f" cy="65" r="5"/></svg>', tick_svg, map_x(coefficients$confidence_low), map_x(coefficients$confidence_high), map_x(coefficients$estimate))
writeLines(svg, file.path(output_root, "hedonic_plot.svg"), useBytes = TRUE)
