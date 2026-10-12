# Confirmatory GLMM (pre-reg 15.1 #3, 15.2, 15.3 #6): lme4::glmer, Laplace approximation (nAGQ = 1).
# Usage: Rscript R/glmer_fit.R <input.csv> <output.json> <formula>
args <- commandArgs(trailingOnly = TRUE)
suppressPackageStartupMessages(library(lme4))
d <- read.csv(args[1], stringsAsFactors = TRUE)
f <- as.formula(args[3])
out <- tryCatch({
  m <- glmer(f, data = d, family = binomial, nAGQ = 1, control = glmerControl(optimizer = "bobyqa"))
  cf <- summary(m)$coefficients
  conv <- m@optinfo$conv$lme4$messages
  list(ok = TRUE, formula = args[3], n = nrow(d), singular = isSingular(m),
       convergence_messages = if (is.null(conv)) list() else as.list(conv),
       fixef = lapply(seq_len(nrow(cf)), function(i) list(term = rownames(cf)[i], estimate = cf[i, 1], se = cf[i, 2], z = cf[i, 3], p = cf[i, 4])),
       re_sd_profile = as.numeric(attr(VarCorr(m)$profile_id, "stddev")), logLik = as.numeric(logLik(m)), lme4_version = as.character(packageVersion("lme4")))
}, error = function(e) list(ok = FALSE, error = conditionMessage(e)))
writeLines(jsonlite_free <- (function(x) { # minimal JSON writer (avoid extra deps)
  enc <- function(v) {
    if (is.null(v)) return("null")
    if (is.list(v)) { if (!is.null(names(v)) && all(names(v) != "")) return(paste0("{", paste(sprintf('"%s":%s', names(v), sapply(v, enc)), collapse = ","), "}"))
                      return(paste0("[", paste(sapply(v, enc), collapse = ","), "]")) }
    if (is.logical(v)) return(if (length(v) == 1) tolower(as.character(v)) else paste0("[", paste(tolower(v), collapse = ","), "]"))
    if (is.numeric(v)) { v[!is.finite(v)] <- NA; s <- ifelse(is.na(v), "null", format(v, digits = 10)); return(if (length(v) == 1) s else paste0("[", paste(s, collapse = ","), "]")) }
    s <- gsub('[[:cntrl:]]', ' ', gsub('"', '\\\\"', as.character(v))); if (length(s) == 1) sprintf('"%s"', s) else paste0("[", paste(sprintf('"%s"', s), collapse = ","), "]")
  }
  enc(x) })(out), args[2])
