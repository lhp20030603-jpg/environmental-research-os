options(warn = 2)

study_column <- __STUDY__
effect_column <- __EFFECT__
variance_column <- __VARIANCE__
confidence_level <- __CONFIDENCE__
leave_one_out_threshold <- __MAX_LOO__

input_path <- "input/data.csv"
output_root <- "output"
dir.create(output_root, showWarnings = FALSE, recursive = FALSE)
data <- utils::read.csv(input_path, check.names = FALSE, stringsAsFactors = FALSE)
yi <- data[[effect_column]]; vi <- data[[variance_column]]; studies <- data[[study_column]]
if (length(yi) < 2L || any(!is.finite(yi)) || any(!is.finite(vi)) || any(vi <= 0)) stop("meta-analysis input is invalid")
fixed_model <- metafor::rma.uni(yi = yi, vi = vi, method = "FE", level = confidence_level * 100)
random_model <- metafor::rma.uni(yi = yi, vi = vi, method = "DL", level = confidence_level * 100)
coefficient <- function(model, term) data.frame(term = term, estimate = as.numeric(model$b), std_error = model$se, conf_low = model$ci.lb, conf_high = model$ci.ub)
utils::write.csv(coefficient(fixed_model, "fixed"), file.path(output_root, "fixed.csv"), row.names = FALSE)
utils::write.csv(coefficient(random_model, "random"), file.path(output_root, "random.csv"), row.names = FALSE)
prediction <- stats::predict(random_model)
utils::write.csv(data.frame(studies = length(yi), q = random_model$QE, i_squared = random_model$I2, tau_squared = random_model$tau2, inverse_variance_support = sum(1 / vi), prediction_low = prediction$pi.lb, prediction_high = prediction$pi.ub), file.path(output_root, "heterogeneity.csv"), row.names = FALSE)
weights <- as.numeric(stats::weights(random_model)); weights <- weights / sum(weights)
study_weights <- data.frame(study = studies, effect = yi, std_error = sqrt(vi), weight = weights)
utils::write.csv(study_weights, file.path(output_root, "study_weights.csv"), row.names = FALSE)
random_effect <- as.numeric(random_model$b)
loo_effect <- vapply(seq_along(yi), function(i) as.numeric(metafor::rma.uni(yi = yi[-i], vi = vi[-i], method = "DL")$b), numeric(1))
utils::write.csv(data.frame(omitted = studies, effect = loo_effect, absolute_change = abs(loo_effect - random_effect)), file.path(output_root, "leave_one_out.csv"), row.names = FALSE)
utils::write.csv(data.frame(study = studies, effect = yi, std_error = sqrt(vi)), file.path(output_root, "funnel.csv"), row.names = FALSE)
utils::write.csv(data.frame(method_id = "meta-analysis", r_version = R.version.string, package_version = as.character(utils::packageVersion("metafor")), confidence_level = confidence_level, model = "fixed-and-dl-random", leave_one_out_threshold = leave_one_out_threshold), file.path(output_root, "package_configuration.csv"), row.names = FALSE)

xml_escape <- function(value) {
  value <- gsub("&", "&amp;", value, fixed = TRUE)
  value <- gsub("<", "&lt;", value, fixed = TRUE)
  value <- gsub(">", "&gt;", value, fixed = TRUE)
  gsub('"', "&quot;", value, fixed = TRUE)
}
height <- 100 + 34 * length(studies); axis_y <- height - 35
critical <- stats::qnorm(1 - (1 - confidence_level) / 2)
x_min <- min(yi - critical * sqrt(vi)); x_max <- max(yi + critical * sqrt(vi)); padding <- max(x_max - x_min, max(abs(c(x_min, x_max)), 1) * 1e-6)
x_min <- x_min - padding * 0.08; x_max <- x_max + padding * 0.08
x <- function(value) 75 + (value - x_min) / (x_max - x_min) * 460
rows <- paste(vapply(seq_along(studies), function(i) { y <- 35 + 34 * i; sprintf('<text x="8" y="%d">%s</text><line x1="%.3f" y1="%d" x2="%.3f" y2="%d" stroke="#555"/><circle cx="%.3f" cy="%d" r="4"/>', y + 4, xml_escape(studies[i]), x(yi[i] - critical * sqrt(vi[i])), y, x(yi[i] + critical * sqrt(vi[i])), y, x(yi[i]), y) }, character(1)), collapse = "")
tick_values <- seq(x_min, x_max, length.out = 5)
ticks <- paste(vapply(tick_values, function(value) sprintf('<g class="x-tick"><line x1="%.3f" y1="%d" x2="%.3f" y2="%d"/><text x="%.3f" y="%d">%.3g</text></g>', x(value), axis_y, x(value), axis_y + 5, x(value), axis_y + 20, value), character(1)), collapse = "")
svg <- sprintf('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="%d" viewBox="0 0 600 %d">%s<line x1="75" y1="%d" x2="535" y2="%d"/>%s</svg>', height, height, rows, axis_y, axis_y, ticks)
writeLines(svg, file.path(output_root, "forest_funnel.svg"), useBytes = TRUE)
