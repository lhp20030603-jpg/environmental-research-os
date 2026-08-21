options(warn = 2)

respondent_column <- __RESPONDENT__; response_column <- __RESPONSE__; bid_column <- __BID__
covariate_columns <- __COVARIATES__; link_name <- __LINK__
confidence_level <- __CONFIDENCE__; max_extreme_share <- __MAX_EXTREME__
max_sensitivity_change <- __MAX_SENSITIVITY__
currency <- __CURRENCY__; price_base <- __PRICE_BASE__
time_basis <- __TIME_BASIS__; population_basis <- __POPULATION_BASIS__

input_path <- "input/data.csv"; output_root <- "output"
dir.create(output_root, showWarnings = FALSE, recursive = FALSE)
data <- utils::read.csv(input_path, check.names = FALSE, stringsAsFactors = FALSE)
quote_id <- function(value) paste0("`", value, "`")
for (column in covariate_columns) data[[column]] <- data[[column]] - mean(data[[column]])
rhs <- paste(c(quote_id(bid_column), vapply(covariate_columns, quote_id, character(1))), collapse = " + ")
formula <- stats::as.formula(sprintf("%s ~ %s", quote_id(response_column), rhs))
model <- stats::glm(formula, data = data, family = stats::binomial(link = link_name))
sensitivity_formula <- stats::as.formula(sprintf("%s ~ %s", quote_id(response_column), quote_id(bid_column)))
sensitivity_model <- stats::glm(sensitivity_formula, data = data, family = stats::binomial(link = link_name))
emit_failure <- function(code, status) {
  cat(paste0("ENVRESEARCH_CODE:", code, "\n"), file = stderr())
  quit(save = "no", status = status, runLast = FALSE)
}
table <- as.data.frame(summary(model)$coefficients); terms <- row.names(table)
bid_index <- match(bid_column, terms); intercept_index <- match("(Intercept)", terms)
if (is.na(bid_index) || is.na(intercept_index)) emit_failure("CV_WTP_UNIDENTIFIED", 41)
beta_bid <- table[bid_index, 1]; beta_intercept <- table[intercept_index, 1]
if (!is.finite(beta_bid) || beta_bid >= 0) emit_failure("CV_BID_SLOPE_INVALID", 42)
probabilities <- stats::fitted(model)
extreme_share <- mean(probabilities <= 0.01 | probabilities >= 0.99)
if (!is.finite(extreme_share) || extreme_share > max_extreme_share) emit_failure("CV_SEPARATION_DETECTED", 43)
bid_values <- sort(unique(data[[bid_column]]))
bid_yes_shares <- data.frame(
  bid = bid_values,
  yes_count = vapply(bid_values, function(value) sum(data[[response_column]][data[[bid_column]] == value]), numeric(1)),
  observations = vapply(bid_values, function(value) sum(data[[bid_column]] == value), integer(1)),
  yes_share = vapply(bid_values, function(value) mean(data[[response_column]][data[[bid_column]] == value]), numeric(1))
)
if (any(diff(bid_yes_shares$yes_share) > 1e-12)) emit_failure("CV_MONOTONICITY_FAILED", 44)

critical <- stats::qnorm(1 - (1 - confidence_level) / 2)
coefficients <- data.frame(term = terms, estimate = table[, 1], std_error = table[, 2], confidence_low = table[, 1] - critical * table[, 2], confidence_high = table[, 1] + critical * table[, 2])
utils::write.csv(coefficients, file.path(output_root, "coefficients.csv"), row.names = FALSE)
covariance <- stats::vcov(model)
covariance_rows <- expand.grid(row_term = terms, column_term = terms, stringsAsFactors = FALSE)
covariance_rows$value <- mapply(function(left, right) covariance[left, right], covariance_rows$row_term, covariance_rows$column_term)
utils::write.csv(covariance_rows, file.path(output_root, "covariance.csv"), row.names = FALSE)

wtp <- -beta_intercept / beta_bid
gradient <- c(-1 / beta_bid, beta_intercept / beta_bid ^ 2)
wtp_covariance <- covariance[c("(Intercept)", bid_column), c("(Intercept)", bid_column), drop = FALSE]
wtp_se <- sqrt(as.numeric(t(gradient) %*% wtp_covariance %*% gradient))
if (!all(is.finite(c(wtp, wtp_se)))) emit_failure("CV_WTP_UNIDENTIFIED", 45)
utils::write.csv(data.frame(name = "median-wtp", estimate = wtp, std_error = wtp_se, confidence_low = wtp - critical * wtp_se, confidence_high = wtp + critical * wtp_se, currency = currency, price_base = price_base, time_basis = time_basis, population_basis = population_basis, transformation = "negative-intercept-over-bid", numerator_term = "(Intercept)", denominator_term = bid_column), file.path(output_root, "wtp.csv"), row.names = FALSE)
utils::write.csv(data.frame(observations = nrow(data), primary_units = length(unique(data[[respondent_column]])), groups = length(unique(data[[bid_column]])), zero_or_no_count = sum(data[[response_column]] == 0)), file.path(output_root, "bid_support.csv"), row.names = FALSE)
utils::write.csv(bid_yes_shares, file.path(output_root, "bid_yes_shares.csv"), row.names = FALSE)
utils::write.csv(data.frame(minimum = min(probabilities), maximum = max(probabilities), extreme_share = extreme_share, max_extreme_share = max_extreme_share), file.path(output_root, "probabilities.csv"), row.names = FALSE)

sensitivity_beta <- stats::coef(sensitivity_model)[[bid_column]]
sensitivity_intercept <- stats::coef(sensitivity_model)[["(Intercept)"]]
sensitivity_wtp <- -sensitivity_intercept / sensitivity_beta
utils::write.csv(data.frame(label = "exclude-covariates", estimate = sensitivity_wtp, baseline_estimate = wtp, absolute_change = abs(sensitivity_wtp - wtp), max_sensitivity_change = max_sensitivity_change, numerator_coefficient = sensitivity_intercept, denominator_coefficient = sensitivity_beta, model_form = link_name), file.path(output_root, "sensitivity.csv"), row.names = FALSE)
utils::write.csv(data.frame(method_id = "contingent-valuation", r_version = R.version.string, confidence_level = confidence_level, cluster_column = "", fixed_effects = "", functional_form = "", family = "", link = link_name), file.path(output_root, "package_configuration.csv"), row.names = FALSE)

axis_min <- min(data[[bid_column]], wtp); axis_max <- max(data[[bid_column]], wtp)
padding <- max(axis_max - axis_min, max(abs(c(axis_min, axis_max)), 1) * 1e-6) * 0.08
axis_min <- axis_min - padding; axis_max <- axis_max + padding
map_x <- function(value) 90 + (value - axis_min) / (axis_max - axis_min) * 480
ticks <- seq(axis_min, axis_max, length.out = 5)
tick_svg <- paste(vapply(ticks, function(value) sprintf('<g class="x-tick"><line x1="%.3f" y1="120" x2="%.3f" y2="126"/><text x="%.3f" y="142">%.3g</text></g>', map_x(value), map_x(value), map_x(value), value), character(1)), collapse = "")
svg <- sprintf('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="170" viewBox="0 0 640 170"><line x1="90" y1="120" x2="570" y2="120"/>%s<circle cx="%.3f" cy="65" r="5"/></svg>', tick_svg, map_x(wtp))
writeLines(svg, file.path(output_root, "cv_plot.svg"), useBytes = TRUE)
