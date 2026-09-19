struct Cart
    items::Vector{String}
end

function add!(cart::Cart, item)
    if !isempty(item)
        push!(cart.items, item)
    end
end
