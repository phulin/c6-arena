-- A science-victory strategy. Strategy and memory live in this file; the shared
-- runtime supplies only permitted observations and already-legal choices.
-- The current native adapter supplies fewer observations/actions than the richer
-- context interface. Missing information is not treated as knowledge of the map.
local memory = {founded = 0, settlers_ordered = 0, visits = {}, trails = {}, last = {}, orders = {}, governors = {}, unit_types = {}}
local function name(option) return string.upper(option.name or option.action or "") end
local function key(x, y) return tostring(x) .. "," .. tostring(y) end
local function clamp(x, a, b) return math.max(a, math.min(b, x)) end
local function count(list, predicate)
    local n = 0
    for _, value in ipairs(list or {}) do if predicate(value) then n = n + 1 end end
    return n
end
local function ranked(names)
    local result = {}
    for i, value in ipairs(names) do result[value] = 200 - i * 2 end
    return result
end
local research = ranked({
    "TECH_POTTERY", "TECH_MINING", "TECH_WRITING", "TECH_ANIMAL_HUSBANDRY",
    "TECH_ARCHERY", "TECH_CURRENCY", "TECH_IRRIGATION", "TECH_THE_WHEEL",
    "TECH_MASONRY", "TECH_BRONZE_WORKING", "TECH_APPRENTICESHIP",
    "TECH_EDUCATION", "TECH_MACHINERY", "TECH_CONSTRUCTION", "TECH_ENGINEERING",
    "TECH_MATHEMATICS", "TECH_CELESTIAL_NAVIGATION", "TECH_SHIPBUILDING",
    "TECH_MILITARY_ENGINEERING", "TECH_CASTLES", "TECH_BANKING", "TECH_PRINTING",
    "TECH_INDUSTRIALIZATION", "TECH_SCIENTIFIC_THEORY", "TECH_CHEMISTRY",
    "TECH_STEAM_POWER", "TECH_ELECTRICITY", "TECH_REPLACEABLE_PARTS",
    "TECH_RADIO", "TECH_FLIGHT", "TECH_ADVANCED_FLIGHT", "TECH_COMBUSTION",
    "TECH_ROCKETRY", "TECH_SATELLITES", "TECH_NUCLEAR_FUSION",
    "TECH_NANOTECHNOLOGY", "TECH_SMART_MATERIALS", "TECH_OFFWORLD_MISSION"
})
local civics = ranked({
    "CIVIC_CODE_OF_LAWS", "CIVIC_FOREIGN_TRADE", "CIVIC_CRAFTSMANSHIP",
    "CIVIC_EARLY_EMPIRE", "CIVIC_STATE_WORKFORCE", "CIVIC_POLITICAL_PHILOSOPHY",
    "CIVIC_RECORDED_HISTORY", "CIVIC_DEFENSIVE_TACTICS", "CIVIC_FEUDALISM",
    "CIVIC_CIVIL_SERVICE", "CIVIC_GUILDS", "CIVIC_EXPLORATION",
    "CIVIC_HUMANISM", "CIVIC_THE_ENLIGHTENMENT", "CIVIC_CIVIL_ENGINEERING",
    "CIVIC_URBANIZATION", "CIVIC_NATIONALISM", "CIVIC_IDEOLOGY",
    "CIVIC_SUFFRAGE", "CIVIC_COLD_WAR", "CIVIC_SPACE_RACE",
    "CIVIC_GLOBALIZATION", "CIVIC_NEAR_FUTURE_GOVERNANCE", "CIVIC_FUTURE_CIVIC"
})
local space_projects = {
    PROJECT_LAUNCH_EARTH_SATELLITE = 1200,
    PROJECT_LAUNCH_MOON_LANDING = 1250,
    PROJECT_LAUNCH_MARS_BASE = 1300,
    PROJECT_LAUNCH_EXOPLANET_EXPEDITION = 1400,
    PROJECT_ORBITAL_LASER = 1350, PROJECT_TERRESTRIAL_LASER = 1350
}
local army = {
    UNIT_SLINGER=true, UNIT_ARCHER=true, UNIT_CROSSBOWMAN=true, UNIT_FIELD_CANNON=true,
    UNIT_WARRIOR=true, UNIT_SWORDSMAN=true, UNIT_MAN_AT_ARMS=true, UNIT_MUSKETMAN=true,
    UNIT_INFANTRY=true, UNIT_MECHANIZED_INFANTRY=true, UNIT_BOMBARD=true,
    UNIT_ARTILLERY=true, UNIT_ANTI_AIR_GUN=true, UNIT_FIGHTER=true,
    UNIT_JET_FIGHTER=true, UNIT_TANK=true, UNIT_MODERN_ARMOR=true
}
local function situation(ctx)
    local o = ctx.observation or {}
    local p = o.player or {}
    local cities, units = {}, {}
    local strongest = nil
    for _, city in ipairs(o.cities or {}) do
        if city.id then cities[city.id] = city end
        if city.x and city.y then cities[key(city.x, city.y)] = city end
        if not strongest or (city.production or 0) > (strongest.production or 0) then strongest = city end
    end
    for _, unit in ipairs(o.units or {}) do if unit.id then units[unit.id] = unit end end
    local city_count = p.city_count
    if city_count == nil then city_count = o.cities and #o.cities or memory.founded end
    local threat = p.threat or 0
    if p.enemy_strength and p.military_strength then
        threat = math.max(threat, clamp(p.enemy_strength / math.max(1, p.military_strength) - 0.5, 0, 2))
    end
    if p.at_war then threat = math.max(threat, 0.8) end
    local phase = "expand"
    if city_count >= 6 or (o.turn and o.turn >= 110) then phase = "develop" end
    if o.known_technologies and o.known_technologies.TECH_ROCKETRY then phase = "space" end
    if p.exoplanet_launched then phase = "accelerate" end
    return {observation=o, player=p, cities=cities, units=units, city_count=city_count,
        threat=threat, phase=phase, strongest=strongest}
end
local function production(option, s)
    local n = name(option)
    local city = s.cities[option.city_id] or s.cities[key(option.x, option.y)] or {}
    local city_key = option.city_id or key(option.x, option.y)
    local ordered = memory.orders[city_key] or {}
    local completed = city.buildings or {}
    local score = -40
    if space_projects[n] then
        score = space_projects[n]
        if (n == "PROJECT_ORBITAL_LASER" or n == "PROJECT_TERRESTRIAL_LASER") and not s.player.exoplanet_launched then score = -100 end
    elseif n == "DISTRICT_SPACEPORT" then
        score = s.phase == "space" and 650 or 40
        if s.strongest and city.id and city.id ~= s.strongest.id then score = score - 220 end
    elseif n == "DISTRICT_CAMPUS" then
        score = 200 + (option.adjacency or 0) * 18
    elseif n == "BUILDING_LIBRARY" then score = 190
    elseif n == "BUILDING_UNIVERSITY" then score = 245
    elseif n == "BUILDING_RESEARCH_LAB" then score = 285
    elseif n == "DISTRICT_INDUSTRIAL_ZONE" then score = 155 + (option.adjacency or 0) * 12
    elseif n == "BUILDING_WORKSHOP" then score = 140
    elseif n == "BUILDING_FACTORY" then score = 210
    elseif n == "BUILDING_COAL_POWER_PLANT" or n == "BUILDING_OIL_POWER_PLANT" or n == "BUILDING_NUCLEAR_POWER_PLANT" then
        score = city.power_shortfall and city.power_shortfall > 0 and 245 or 100
    elseif n == "UNIT_SETTLER" then
        local settlers = s.observation.units and count(s.observation.units, function(u) return u.type == "UNIT_SETTLER" end)
            or math.max(0, memory.settlers_ordered - math.max(0, memory.founded - 1))
        score = s.phase == "expand" and 170 or 10
        score = score - settlers * 110 - s.threat * 230
        if s.observation.expansion_sites ~= nil and s.observation.expansion_sites <= 0 then score = -200 end
        if (city.population or 3) < 3 then score = score - 100 end
        if s.city_count >= 8 then score = -150 end
        if s.player.amenities and s.player.amenities < -2 then score = score - 100 end
    elseif n == "UNIT_SCOUT" then
        local scouts = s.observation.units and count(s.observation.units, function(u) return u.type == "UNIT_SCOUT" end)
            or (ordered[n] or 0)
        score = 130 - scouts * 150
        if s.phase ~= "expand" then score = score - 140 end
    elseif n == "UNIT_BUILDER" then
        local builders = s.observation.units and count(s.observation.units, function(u) return u.type == "UNIT_BUILDER" end)
            or (ordered[n] or 0)
        score = 100 + math.min(100, (city.unimproved_worked_tiles or 0) * 30) - builders * 70
    elseif n == "UNIT_TRADER" then
        score = (s.player.trade_routes_available or 0) > 0 and 185 or -80
    elseif army[n] then
        local soldiers = s.observation.units and count(s.observation.units, function(u) return army[u.type] end)
            or (ordered[n] or 0)
        local desired = math.max(2, math.ceil(s.city_count / 2))
        score = 60 + s.threat * 270 + math.max(0, desired - soldiers) * 55
        if n == "UNIT_ARCHER" or n == "UNIT_CROSSBOWMAN" then score = score + 35 end
        if n == "UNIT_WARRIOR" and soldiers >= desired then score = score - 150 end
    elseif n == "BUILDING_WALLS" or n == "BUILDING_CASTLE" then
        score = (city.border or city.under_siege) and 80 + s.threat * 250 or -30
    elseif n == "BUILDING_GRANARY" then
        score = city.housing and city.population and city.housing - city.population <= 1 and 175 or 75
    elseif n == "BUILDING_MONUMENT" then score = 110
    elseif n == "BUILDING_WATER_MILL" then score = 85
    elseif n == "DISTRICT_COMMERCIAL_HUB" or n == "DISTRICT_HARBOR" then
        score = (s.player.gold_per_turn or 1) < 0 and 250 or 100
    elseif n == "BUILDING_MARKET" or n == "BUILDING_LIGHTHOUSE" or n == "BUILDING_BANK" then
        score = (s.player.gold_per_turn or 1) < 0 and 260 or 125
    elseif n == "DISTRICT_AQUEDUCT" or n == "BUILDING_SEWER" then
        score = city.housing and city.population and city.housing <= city.population and 210 or 20
    elseif n == "DISTRICT_ENTERTAINMENT_COMPLEX" or n == "BUILDING_ARENA" then
        score = (city.amenities or 0) < -1 and 230 or 10
    elseif n == "PROJECT_RESEARCH_GRANTS" then score = 55
    elseif n == "REPAIR" then score = 300
    elseif n == "PROJECT_CARBON_RECAPTURE" then score = 5
    end
    if completed[n] and n:sub(1, 9) == "BUILDING_" then score = -1000 end
    if s.threat > 0.7 and army[n] then score = score + 100 end
    if city.under_siege and (space_projects[n] or n == "UNIT_SETTLER") then score = score - 900 end
    if not space_projects[n] then score = score - math.min(60, (option.production_turns or 0) * 1.5) end
    return score
end
local function unit_action(option, s)
    local n = name(option)
    local unit = s.units[option.actor] or {}
    local unit_type = unit.type or memory.unit_types[option.actor]
    local hp = unit.health and unit.health / (unit.max_health or 100) or 1
    local civilian = unit.role == "settler" or unit.role == "builder" or unit.role == "trader"
        or unit_type == "UNIT_SETTLER" or unit_type == "UNIT_BUILDER" or unit_type == "UNIT_TRADER"
    local score = -50
    if n == "FOUND_CITY" then
        score = s.city_count == 0 and 1000 or 600
        score = score + (option.fresh_water and 40 or 0) + (option.food_yield or 0) * 8 + (option.production_yield or 0) * 8
        score = score - (option.threat or 0) * 500
        if option.loyalty_per_turn and option.loyalty_per_turn < -10 then score = score - 800 end
    elseif n == "RANGED_ATTACK" or n == "ATTACK" or n == "BOMBARD" then
        score = 100 + (option.damage_dealt or 0) * 3 - (option.damage_taken or 0) * 4
        if option.captures_city then score = score + 180 end
        if option.defends_city then score = score + 120 end
        if option.target_health and option.damage_dealt and option.damage_dealt >= option.target_health then score = score + 100 end
        if civilian then score = -1000 end
        if hp < 0.35 and not option.captures_city then score = score - 200 end
    elseif n == "HEAL" then
        score = (1 - hp) * 420 - 20 - (option.threat or 0) * 100
    elseif n == "FORTIFY" then
        score = hp < 0.7 and 160 or 5
        if unit.role == "garrison" or option.defends_city then score = score + 75 end
    elseif n == "MOVE" then
        score = 20
        local visits = memory.visits[option.actor] or {}
        local position = key(option.x, option.y)
        score = score - (visits[position] or 0) * 35
        if memory.last[option.actor] == position then score = score - 55 end
        score = score + (option.unrevealed_neighbors or 0) * 16 + (option.goody_hut and 110 or 0)
        if option.destination_distance and option.origin_distance then
            score = score + (option.origin_distance - option.destination_distance) * 35
        end
        local danger = option.threat or 0
        score = score - danger * (civilian and 450 or hp < 0.5 and 250 or 70)
        if option.escorts_settler then score = score + 70 end
        if option.defends_city then score = score + 100 end
        if option.retreats and hp < 0.5 then score = score + 220 end
    elseif n == "BUILD_IMPROVEMENT" or n == "REPAIR_IMPROVEMENT" or n == "REPAIR" or n == "REPAIR_ROUTE" or n == "REMOVE_FEATURE" then
        score = 100 + (option.food_gain or 0) * 30 + (option.production_gain or 0) * 40 + (option.science_gain or 0) * 35
        if option.connects_resource then score = score + 85 end
        if option.powers_spaceport then score = score + 160 end
        if option.threat and option.threat > 0.5 then score = score - 400 end
        if n == "REMOVE_FEATURE" and not option.production_gain then score = score - 150 end
    elseif n == "UPGRADE" then
        score = s.threat > 0.5 and 200 or 40
        if (s.player.treasury or 0) - (option.gold_cost or 0) < 100 then score = score - 150 end
    elseif n == "PROMOTE" then score = 150 + (option.heals_unit and (1-hp)*200 or 0)
    elseif n == "SLEEP" or n == "SKIP" then score = -10
    elseif n == "FORM_CORPS" or n == "FORM_ARMY" then score = 80 + s.threat * 50
    elseif n == "ESTABLISH_TRADE_ROUTE" then
        score = 100 + (option.food or 0) * 18 + (option.production or 0) * 25 + (option.gold or 0) * 8 + (option.science or 0) * 20
        score = score - (option.threat or 0) * 200
    elseif n == "COUNTERSPY" then score = option.protects_spaceport and 250 or 60
    elseif n == "SPY_OPERATION" then score = option.steals_technology and 120 or 30
    elseif n == "ACTIVATE_GREAT_PERSON" then score = 200 + (option.science or 0) * 2 + (option.space_production or 0) * 3
    elseif n == "SPREAD_RELIGION" then score = 10
    elseif n == "REBASE" then score = 40 + (option.defends_city and 80 or 0)
    elseif n == "DELETE_UNIT" then score = -900
    end
    return score
end
local function diplomacy(option, s)
    local n = name(option)
    local reserve = s.phase == "space" and 600 or 150
    local cost = option.gold_cost or 0
    local score = -80
    if n == "DECLARE_FRIENDSHIP" or n == "ACCEPT_FRIENDSHIP" or n == "RESEARCH_ALLIANCE" then score = 150
    elseif n == "MAKE_PEACE" or n == "ACCEPT_PEACE" then score = 100 + s.threat * 150
    elseif n == "DECLARE_WAR" then score = -500
    elseif n == "OPEN_BORDERS" then score = option.reciprocal and 60 or 5
    elseif n == "TRADE" or n == "ACCEPT_DEAL" then
        score = (option.gold_received or 0) - cost + (option.gold_per_turn_received or 0) * 15
        score = score + (option.new_luxury and 100 or 0) + (option.needed_resource and 80 or 0)
        score = score - (option.strategic_resource_given and 180 or 0)
    elseif n == "DENOUNCE" then score = -100
    elseif n == "DECLINE" or n == "PASS" then score = 0
    end
    if cost > 0 and (s.player.treasury or 0) - cost < reserve then score = score - 400 end
    return score
end
local function score_option(ctx, option, s)
    for field, value in pairs(option.metadata or {}) do
        if option[field] == nil then option[field] = value end
    end
    local n, kind = name(option), ctx.kind
    if kind == "unit_selection" then
        if n == "UNIT_SETTLER" then return 200 end
        if n == "UNIT_SCOUT" then return 150 end
        if n == "UNIT_BUILDER" then return 100 end
        return 10
    end
    if kind == "production" or kind == "production_control" then
        if n == "KEEP_PRODUCTION" then return 10000 end
        return production(option, s)
    end
    if kind == "unit" then return unit_action(option, s) end
    if kind == "research" or kind == "civic" then
        local scores = kind == "research" and research or civics
        local score = scores[n] or 0
        if s.threat > 0.6 and (n == "TECH_ARCHERY" or n == "TECH_MACHINERY" or n == "TECH_STEEL") then score = score + 300 end
        if s.phase == "space" or s.phase == "accelerate" then
            if n == "TECH_SMART_MATERIALS" or n == "TECH_OFFWORLD_MISSION" then score = 700
            elseif n == "TECH_ROCKETRY" or n == "TECH_SATELLITES" or n == "TECH_NANOTECHNOLOGY" then score = 650
            elseif n == "CIVIC_SPACE_RACE" or n == "CIVIC_GLOBALIZATION" then score = 600 end
        end
        if option.boosted then score = score + 18 end
        return score - math.min(30, option.turns_to_complete or 0)
    end
    if kind == "commemoration" then
        local scores = {COMMEMORATION_SCIENTIFIC=150, COMMEMORATION_INFRASTRUCTURE=100,
            COMMEMORATION_INDUSTRIAL=170, COMMEMORATION_AERONAUTICAL=200, COMMEMORATION_ECONOMIC=90}
        return (scores[n] or 0) + (s.phase == "expand" and n == "COMMEMORATION_INFRASTRUCTURE" and 100 or 0)
    end
    if kind == "diplomacy" or kind == "trade" then return diplomacy(option, s) end
    if kind == "player_action" then
        if n == "RECRUIT_GREAT_PERSON" then return 180 end
        if n == "GIVE_INFLUENCE_TOKEN" then return 40 end
        if n == "DIPLOMACY_MAKE_PEACE" then return 100 end
        if n == "LEVY_MILITARY" then return s.threat > 0.8 and 60 or -100 end
        return n == "PASS" and 0 or -50
    end
    if kind == "city_economy" then
        if n == "PASS" then return 0 end
        if n:sub(1,9) == "BUILDING_" or n:sub(1,9) == "DISTRICT_" or n:sub(1,5) == "UNIT_" then
            local reserve = option.currency == "gold" and 1000 or 500
            local balance = option.currency == "gold" and (s.player.treasury or 0) or (s.player.faith or 0)
            return balance > reserve and production(option,s) - 160 or -100
        end
        return -10
    end
    if kind == "great_work" then return (n == "KEEP" or n == "PASS" or n == "KEEP_GREAT_WORKS") and 10 or 0 end
    if kind == "government" then
        local scores = {GOVERNMENT_CLASSICAL_REPUBLIC=100, GOVERNMENT_MERCHANT_REPUBLIC=180,
            GOVERNMENT_DEMOCRACY=250, GOVERNMENT_SYNTHETIC_TECHNOCRACY=350}
        return (scores[n] or 0) - (option.anarchy_turns or 0) * 40
    end
    if kind == "policy" then
        local scores = {POLICY_URBAN_PLANNING=80, POLICY_DISCIPLINE=50, POLICY_COLONIZATION=110,
            POLICY_NATURAL_PHILOSOPHY=160, POLICY_RATIONALISM=200, POLICY_FIVE_YEAR_PLAN=220,
            POLICY_INTERNATIONAL_SPACE_AGENCY=300, POLICY_INTEGRATED_SPACE_CELL=310,
            POLICY_SERFDOM=120, POLICY_PUBLIC_WORKS=150, POLICY_PROFESSIONAL_ARMY=70}
        local score = scores[n] or 0
        if n == "POLICY_COLONIZATION" and s.phase ~= "expand" then score = -50 end
        if n == "POLICY_DISCIPLINE" and s.threat > 0.5 then score = score + 100 end
        return score
    end
    if kind == "governor" then
        if n == "ASSIGN_GOVERNOR" then
            local city = s.cities[option.value] or s.cities[key(option.x, option.y)] or {}
            local educator = memory.governors[option.actor] == "GOVERNOR_THE_EDUCATOR"
            local score = (city.population or 0) * 6 + (city.production or 0) * 2
            if educator then score = score + (city.science or 0) * 8 end
            if memory.capital and key(option.x, option.y) == memory.capital then score = score + 50 end
            return score
        end
        local scores = {GOVERNOR_THE_EDUCATOR=180, GOVERNOR_THE_RESOURCE_MANAGER=160,
            GOVERNOR_THE_BUILDER=130, GOVERNOR_THE_DEFENDER=50,
            GOVERNOR_PROMOTION_EDUCATOR_RESEARCHER=260,
            GOVERNOR_PROMOTION_EDUCATOR_CONNOISSEUR=205,
            GOVERNOR_PROMOTION_EDUCATOR_GRANTS=220,
            GOVERNOR_PROMOTION_EDUCATOR_SPACE_INITIATIVE=140,
            GOVERNOR_PROMOTION_RESOURCE_MANAGER_INDUSTRIALIST=230,
            GOVERNOR_PROMOTION_RESOURCE_MANAGER_VERTICAL_INTEGRATION=240,
            GOVERNOR_PROMOTION_RESOURCE_MANAGER_SURPLUS_LOGISTICS=180,
            GOVERNOR_PROMOTION_RESOURCE_MANAGER_EXPEDITION=190}
        local score = scores[n] or 0
        if n == "GOVERNOR_PROMOTION_EDUCATOR_SPACE_INITIATIVE" and
            (s.phase == "space" or s.phase == "accelerate") then score = 400 end
        if option.space_production then score = score + option.space_production * 20 end
        if option.science then score = score + option.science * 15 end
        if option.defends_city then score = score + s.threat * 200 end
        if option.loyalty_gain then score = score + option.loyalty_gain * 10 end
        return score
    end
    if kind == "great_person" then
        if n == "PASS" or n == "DECLINE" then return 0 end
        local score = (option.science or 0) + (option.space_production or 0) * 3
        if option.person_class == "scientist" then score = score + 180 end
        if option.person_class == "engineer" then score = score + 130 end
        if option.gold_cost and (s.player.treasury or 0) - option.gold_cost < 200 then score = score - 500 end
        return score
    end
    if kind == "congress" then
        if n == "VOTES" then return -(option.value or 1) end
        if option.resolution_name == "WC_RES_DIPLOVICTORY" then
            -- This policy targets the space race; do not spend favor gaining
            -- diplomatic victory points before completing it.
            if option.value == 2 and option.target_hash == ctx.owner then return 100 end
            if option.value == 1 and option.target_hash == ctx.owner then return -1000 end
        end
        local score = (option.science_bonus or 0) * 20 + (option.production_bonus or 0) * 20
        if option.blocks_own_victory then score = score - 500 end
        if option.favor_cost and option.favor_cost > (s.player.favor or 0) / 3 then score = score - 150 end
        if n == "ABSTAIN" then score = score - 5 end
        return score
    end
    if kind == "city_citizens" then
        return (option.food_surplus or 0) * 6 + (option.production or 0) * (s.phase == "space" and 15 or 9)
            + (option.science or 0) * 10 - (option.starvation and 1000 or 0)
    end
    if kind == "purchase" then
        local score = production(option, s) - 100
        if (s.player.treasury or 0) - (option.gold_cost or 0) < (s.phase == "space" and 600 or 150) then score = score - 1000 end
        if n == "PASS" or n == "DECLINE" then score = 0 end
        return score
    end
    if kind == "religion" then
        local scores = {BELIEF_DIVINE_SPARK=100, BELIEF_RELIGIOUS_SETTLEMENTS=130, BELIEF_FERTILITY_RITES=70}
        return (scores[n] or 0) - (option.faith_cost or 0) * 0.1
    end
    error("science strategy has no handler for decision kind: " .. tostring(kind))
end
return function(ctx)
    assert(type(ctx.options) == "table" and #ctx.options > 0, "no legal options")
    if ctx.kind == "unit_selection" then
        for _, option in ipairs(ctx.options) do memory.unit_types[option.actor] = name(option) end
    end
    local s = situation(ctx)
    local best = ctx.random_index or 1
    local best_score = score_option(ctx, ctx.options[best], s)
    for i, option in ipairs(ctx.options) do
        local score = score_option(ctx, option, s)
        if score > best_score then best, best_score = i, score end
    end
    local chosen = ctx.options[best]
    if ctx.kind == "unit" then
        if name(chosen) == "FOUND_CITY" then
            if memory.founded == 0 and chosen.origin_x and chosen.origin_y then
                memory.capital = key(chosen.origin_x, chosen.origin_y)
            end
            memory.founded = memory.founded + 1
        end
        if name(chosen) == "MOVE" and chosen.x and chosen.y then
            local actor, position = chosen.actor or 0, key(chosen.x, chosen.y)
            memory.visits[actor] = memory.visits[actor] or {}
            local visits = memory.visits[actor]
            visits[position] = (visits[position] or 0) + 1
            local trail = memory.trails[actor] or {cursor=1}
            local old = trail[trail.cursor]
            if old then
                visits[old] = visits[old] - 1
                if visits[old] == 0 then visits[old] = nil end
            end
            trail[trail.cursor] = position
            trail.cursor = trail.cursor % 32 + 1
            memory.trails[actor] = trail
            if chosen.origin_x and chosen.origin_y then memory.last[actor] = key(chosen.origin_x, chosen.origin_y) end
        end
    elseif ctx.kind == "production" then
        local city = chosen.city_id or key(chosen.x, chosen.y)
        memory.orders[city] = memory.orders[city] or {}
        memory.orders[city][name(chosen)] = (memory.orders[city][name(chosen)] or 0) + 1
        if name(chosen) == "UNIT_SETTLER" then memory.settlers_ordered = memory.settlers_ordered + 1 end
    elseif ctx.kind == "governor" and name(chosen):sub(1, 13) == "GOVERNOR_THE_" then
        memory.governors[chosen.actor or 0] = name(chosen)
    end
    return best, string.format("science/%s %s %s score=%.1f threat=%.2f", s.phase, ctx.kind, name(chosen), best_score, s.threat)
end
