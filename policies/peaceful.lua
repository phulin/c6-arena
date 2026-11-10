-- A quiet reference opponent for exercising long native games.
-- Establish a capital, defend it, and leave optional decisions alone.
return function(ctx)
    local function find(wanted)
        for i, option in ipairs(ctx.options) do
            if string.upper(option.name or "") == wanted then return i end
        end
    end
    for _, wanted in ipairs({"PASS", "KEEP_PRODUCTION", "KEEP_GREAT_WORKS", "POSITIVE", "ACKNOWLEDGE"}) do
        local i = find(wanted)
        if i then return i end
    end
    if ctx.kind == "unit" then
        for _, wanted in ipairs({"FOUND_CITY", "FORTIFY", "SKIP"}) do
            local i = find(wanted)
            if i then return i end
        end
    elseif ctx.kind == "production" or ctx.kind == "production_control" then
        for _, wanted in ipairs({"BUILDING_MONUMENT", "BUILDING_GRANARY", "BUILDING_WATER_MILL",
            "UNIT_ARCHER", "UNIT_SLINGER", "UNIT_WARRIOR", "UNIT_SPEARMAN", "UNIT_CROSSBOWMAN", "PROJECT_RESEARCH_GRANTS"}) do
            local i = find(wanted)
            if i then return i end
        end
    elseif ctx.kind == "congress" then
        local selected
        for i, option in ipairs(ctx.options) do
            if option.name == "votes" and (not selected or option.value < ctx.options[selected].value) then selected = i end
        end
        if selected then return selected end
        for i, option in ipairs(ctx.options) do
            local m = option.metadata or {}
            if m.resolution_name == "WC_RES_DIPLOVICTORY" and m.target_hash == ctx.owner and option.value == 2 then return i end
        end
    end
    return ctx.random_index
end
