options(warn = 2)

unit_column <- __UNIT__; visits_column <- __VISITS__; cost_column <- __COST__
exposure_column <- __EXPOSURE__; site_column <- __SITE__
substitute_columns <- __SUBSTITUTES__; cluster_column <- __CLUSTER__
family_name <- __FAMILY__; confidence_level <- __CONFIDENCE__
max_dispersion <- __MAX_DISPERSION__; max_sensitivity_change <- __MAX_SENSITIVITY__
currency <- __CURRENCY__; price_base <- __PRICE_BASE__
time_basis <- __TIME_BASIS__; population_basis <- __POPULATION_BASIS__

input_path <- "input/data.csv"; output_root <- "output"
dir.create(output_root, showWarnings = FALSE, recursive = FALSE)
managed_library <- normalizePath(Sys.getenv("R_LIBS_USER"), mustWork = TRUE)
required_package <- if (family_name == "poisson") "fixest" else "MASS"
invisible(loadNamespace(required_package, lib.loc = managed_library))
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
site_effect <- sprintf("factor(%s)", quote_id(site_column))
rhs <- paste(c(quote_id(cost_column), vapply(substitute_columns, quote_id, character(1)), site_effect, sprintf("stats::offset(log(%s))", quote_id(exposure_column))), collapse = " + ")
formula <- stats::as.formula(sprintf("%s ~ %s", quote_id(visits_column), rhs))
vcov_value <- if (is.null(cluster_column)) "hetero" else stats::as.formula(paste("~", quote_id(cluster_column)))
model <- if (family_name == "poisson") fixest::fepois(formula, data = data, vcov = vcov_value) else MASS::glm.nb(formula, data = data)
audit_managed_namespaces()
table <- if (family_name == "poisson") as.data.frame(fixest::coeftable(model)) else as.data.frame(summary(model)$coefficients)
terms <- row.names(table); index <- match(cost_column, terms)
if (is.na(index)) {
  cat("ENVRESEARCH_CODE:TRAVEL_COST_SLOPE_INVALID\n", file = stderr())
  quit(save = "no", status = 32, runLast = FALSE)
}
critical <- stats::qnorm(1 - (1 - confidence_level) / 2)
beta <- table[index, 1]; beta_se <- table[index, 2]
coefficients <- data.frame(term = cost_column, estimate = beta, std_error = beta_se, confidence_low = beta - critical * beta_se, confidence_high = beta + critical * beta_se)
utils::write.csv(coefficients, file.path(output_root, "coefficients.csv"), row.names = FALSE)
utils::write.csv(data.frame(row_term = cost_column, column_term = cost_column, value = beta_se ^ 2), file.path(output_root, "covariance.csv"), row.names = FALSE)

consumer_surplus <- -1 / beta; consumer_se <- beta_se / (beta ^ 2)
utils::write.csv(data.frame(name = "consumer-surplus", estimate = consumer_surplus, std_error = consumer_se, confidence_low = consumer_surplus - critical * consumer_se, confidence_high = consumer_surplus + critical * consumer_se, currency = currency, price_base = price_base, time_basis = time_basis, population_basis = population_basis, transformation = "negative-inverse-cost", numerator_term = "", denominator_term = cost_column), file.path(output_root, "consumer_surplus.csv"), row.names = FALSE)
utils::write.csv(data.frame(observations = nrow(data), primary_units = length(unique(data[[unit_column]])), groups = length(unique(data[[site_column]])), zero_or_no_count = sum(data[[visits_column]] == 0)), file.path(output_root, "support.csv"), row.names = FALSE)
dispersion <- sum(stats::residuals(model, type = "pearson") ^ 2) / stats::df.residual(model)
theta <- if (family_name == "negative-binomial") model$theta else NA_real_
utils::write.csv(data.frame(dispersion = dispersion, max_dispersion = max_dispersion, log_likelihood = as.numeric(stats::logLik(model)), deviance = stats::deviance(model), residual_df = stats::df.residual(model), theta = theta), file.path(output_root, "dispersion.csv"), row.names = FALSE, na = "")
utils::write.csv(data.frame(row_index = seq_len(nrow(data)), observed = data[[visits_column]], fitted = stats::fitted(model)), file.path(output_root, "fit_evidence.csv"), row.names = FALSE)

sensitivity_rhs <- paste(c(quote_id(cost_column), site_effect, sprintf("stats::offset(log(%s))", quote_id(exposure_column))), collapse = " + ")
sensitivity_formula <- stats::as.formula(sprintf("%s ~ %s", quote_id(visits_column), sensitivity_rhs))
sensitivity_model <- if (family_name == "poisson") fixest::fepois(sensitivity_formula, data = data, vcov = vcov_value) else MASS::glm.nb(sensitivity_formula, data = data)
audit_managed_namespaces()
sensitivity_beta <- stats::coef(sensitivity_model)[[cost_column]]
sensitivity_surplus <- -1 / sensitivity_beta
utils::write.csv(data.frame(label = "exclude-substitute-controls", estimate = sensitivity_surplus, baseline_estimate = consumer_surplus, absolute_change = abs(sensitivity_surplus - consumer_surplus), max_sensitivity_change = max_sensitivity_change, raw_coefficient = sensitivity_beta, model_form = family_name), file.path(output_root, "sensitivity.csv"), row.names = FALSE)
utils::write.csv(data.frame(method_id = "travel-cost", r_version = R.version.string, confidence_level = confidence_level, cluster_column = if (is.null(cluster_column)) "" else cluster_column, fixed_effects = site_column, functional_form = "", family = family_name, link = ""), file.path(output_root, "package_configuration.csv"), row.names = FALSE)

axis_min <- min(coefficients$confidence_low, 0); axis_max <- max(coefficients$confidence_high, 0)
padding <- max(axis_max - axis_min, max(abs(c(axis_min, axis_max)), 1) * 1e-6) * 0.08
axis_min <- axis_min - padding; axis_max <- axis_max + padding
map_x <- function(value) 90 + (value - axis_min) / (axis_max - axis_min) * 480
ticks <- seq(axis_min, axis_max, length.out = 5)
tick_svg <- paste(vapply(ticks, function(value) sprintf('<g class="x-tick"><line x1="%.3f" y1="120" x2="%.3f" y2="126"/><text x="%.3f" y="142">%.3g</text></g>', map_x(value), map_x(value), map_x(value), value), character(1)), collapse = "")
svg <- sprintf('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="170" viewBox="0 0 640 170"><line x1="90" y1="120" x2="570" y2="120"/>%s<line x1="%.3f" y1="65" x2="%.3f" y2="65" stroke="#2369a8"/><circle cx="%.3f" cy="65" r="5"/></svg>', tick_svg, map_x(coefficients$confidence_low), map_x(coefficients$confidence_high), map_x(coefficients$estimate))
writeLines(svg, file.path(output_root, "travel_cost_plot.svg"), useBytes = TRUE)
