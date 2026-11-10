-- Integration policy for two native human seats. Use a regional-contact
-- fixture; seat 0 sends a delegation, gives 10 gold, declares war, then offers
-- peace once native rules permit it. Seat 1 responds through its own turn.
local stage, since = 0, 0
return function(ctx)
    local turn = (ctx.observation or {}).turn or 0
    local function find(n, actor)
        for i, option in ipairs(ctx.options) do
            if option.name == n and (actor == nil or option.actor == actor) then return i end
        end
    end
    local function choose(i)
        return i, string.format("diplomacy-probe stage=%d turn=%d cash=%.3f", stage, turn,
            ((ctx.observation or {}).player or {}).treasury or 0)
    end
    if ctx.kind == "diplomacy" and ctx.owner == 0 then
        local action = stage == 0 and "DIPLOMATIC_DELEGATION" or stage == 1 and "PROPOSE_TRADE"
            or stage == 3 and "DECLARE_SURPRISE_WAR" or stage == 4 and "PROPOSE_PEACE_DEAL"
        local i = action and find(action, 1)
        if i and (stage == 0 or turn >= since + 2) then
            stage, since = stage + 1, turn
            return choose(i)
        end
    elseif ctx.kind == "diplomacy_deal" then
        local i = find("PROPOSE")
        if i then
            if stage == 2 then stage = 3 elseif stage == 5 then stage = 6 end
            return choose(i)
        end
        i = find("GOLD", ctx.owner)
        if i then return choose(i) end
    elseif ctx.kind == "diplomacy_deal_counter" then
        local i = find("ACCEPT")
        if i then return choose(i) end
    elseif ctx.kind == "diplomacy_deal_item" then
        for i, option in ipairs(ctx.options) do
            if option.name:sub(1,5) == "GOLD:" and (option.metadata or {}).duration == 0 then return choose(i) end
        end
    elseif ctx.kind == "diplomacy_deal_amount" then
        for i, option in ipairs(ctx.options) do
            local m = option.metadata or {}
            if (m.amount_min or option.value) <= 10 and (m.amount_max or option.value) >= 10 then return choose(i) end
        end
    end
    for _, n in ipairs({"POSITIVE", "ACCEPT", "ACKNOWLEDGE", "PASS", "KEEP_PRODUCTION", "KEEP_GREAT_WORKS", "BACK"}) do
        local i = find(n)
        if i then return choose(i) end
    end
    if ctx.kind == "unit" then
        for _, n in ipairs({"found_city", "fortify", "skip"}) do
            local i = find(n)
            if i then return choose(i) end
        end
    elseif ctx.kind == "production" or ctx.kind == "production_control" then
        for _, n in ipairs({"BUILDING_MONUMENT", "BUILDING_GRANARY", "UNIT_WARRIOR", "UNIT_SLINGER"}) do
            local i = find(n)
            if i then return choose(i) end
        end
    end
    return choose(ctx.random_index)
end
