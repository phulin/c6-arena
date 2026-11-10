-- Found immediately when legal. Otherwise retain the native random baseline.
-- This is a starting point for editing, not a claim of strong Civ play.
return function(ctx)
    if ctx.kind == "unit" then
        for i, option in ipairs(ctx.options) do
            if option.name == "found_city" then return i end
        end
    end
    return ctx.random_index
end
