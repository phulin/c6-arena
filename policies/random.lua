-- Native policy RNG supplies a reproducible legal fallback for every decision.
return function(ctx)
    return ctx.random_index
end
