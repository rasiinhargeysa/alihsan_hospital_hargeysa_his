// Ward Management page
// Groups IPD rooms by floor, shows rate/ward pulled from
// "Healthcare Service Unit Type", and supports live search across
// room, bed, floor and rate.

frappe.pages['ward-management'].on_page_load = function (wrapper) {
    new WardManagement(wrapper)
}

WardManagement = Class.extend({

    init: function (wrapper) {
        this.page = frappe.ui.make_app_page({
            parent: wrapper,
            title: "Ward Management",
            single_column: true
        });
        $('.page-head').hide()
        this.make()
    },

    make: function () {
        let me = this
        $(frappe.render_template("ward_management", me)).appendTo(me.page.main)

        this.render_bed_status_chart()
        this.setup_search()
        this.load_wards()
    },

    // ---------------------------------------------------------------
    // Bed status doughnut chart
    // ---------------------------------------------------------------
    render_bed_status_chart: function () {
        frappe.call({
            method: 'his.api.ipd_dashboard.bed_status',
            callback: function (data) {
                if (!data || !data.message) return

                let xValues2 = ["Occupied", "In Cleaning", "Discharge Ordered", "Vacant"]
                let yValues2 = []
                let barColors2 = ["#b91d47", "#ffff00", "#ffa500", "#1e7145"]

                data.message.forEach(row => {
                    yValues2.push(row.Occupied, row.InCleaning, row.Discharge, row.Vacant)
                })

                new Chart("myChart2", {
                    type: "doughnut",
                    data: {
                        labels: xValues2,
                        datasets: [{
                            backgroundColor: barColors2,
                            data: yValues2,
                        }],
                    },
                    options: {
                        title: {
                            display: true,
                            text: "Bed Status",
                        },
                    },
                });
            }
        })
    },

    // ---------------------------------------------------------------
    // Search
    // ---------------------------------------------------------------
    setup_search: function () {
        let me = this
        $('#bed_search_input').on('keyup', function () {
            me.filter_beds($(this).val().trim().toLowerCase())
        })
    },

    filter_beds: function (term) {
        $('.no_search_results').remove()

        if (!term) {
            $('.bed_card, .room_block, .floor_block').show()
            return
        }

        let any_visible = false

        $('.floor_block').each(function () {
            let floor_visible = false

            $(this).find('.room_block').each(function () {
                let room_visible = false

                $(this).find('.bed_card').each(function () {
                    let $bed = $(this)
                    let match = ($bed.data('bed') || '').toString().includes(term) ||
                        ($bed.data('room') || '').toString().includes(term) ||
                        ($bed.data('floor') || '').toString().includes(term) ||
                        ($bed.data('rate') || '').toString().includes(term) ||
                        ($bed.data('ward') || '').toString().includes(term)

                    $bed.toggle(match)
                    if (match) {
                        room_visible = true
                        any_visible = true
                    }
                })

                $(this).toggle(room_visible)
                if (room_visible) floor_visible = true
            })

            $(this).toggle(floor_visible)
        })

        if (!any_visible) {
            $('#room').append('<div class="no_results no_search_results">No rooms or beds match your search.</div>')
        }
    },

    // ---------------------------------------------------------------
    // Data loading: load everything up front (room types, then all
    // their beds in a single query), then group/render client-side.
    // ---------------------------------------------------------------
    load_wards: function () {
        let me = this

        frappe.db.get_list('Healthcare Service Unit Type', {
            fields: ['name', 'floor', 'rate', 'ward'],
            filters: { type: "IPD" , disabled:0},
            limit: 1000
        }).then(room_types => {
            if (!room_types.length) {
                $('#room').html('<div class="no_results">No IPD wards found.</div>')
                return
            }

            let room_type_names = room_types.map(rt => rt.name)

            // Single bulk query for every bed across every room type,
            // instead of one query per room type.
            return frappe.db.get_list('Healthcare Service Unit', {
                fields: ['name', 'occupancy_status', 'patient', 'service_unit_type'],
                filters: { service_unit_type: ["in", room_type_names, ] , disabled: 0},
                limit: 5000
            }).then(all_beds => {
                me.render_floors(room_types, all_beds)
            })
        })
    },

    render_floors: function (room_types, all_beds) {
        let me = this

        // Group beds by their room (service_unit_type) for fast lookup
        let beds_by_room = {}
        all_beds.forEach(bed => {
            beds_by_room[bed.service_unit_type] = beds_by_room[bed.service_unit_type] || []
            beds_by_room[bed.service_unit_type].push(bed)
        })

        // Group room types by floor
        let floors = {}
        room_types.forEach(rt => {
            let floor_name = rt.floor || "Unassigned Floor"
            floors[floor_name] = floors[floor_name] || []
            floors[floor_name].push(rt)
        })

        let floor_names = Object.keys(floors).sort()

        let floors_html = floor_names.map(floor_name => {
            let room_types_in_floor = floors[floor_name]
            let rooms_html = room_types_in_floor
                .map(rt => me.render_room_block(rt, floor_name, beds_by_room[rt.name] || []))
                .join('')

            let bed_count = room_types_in_floor.reduce((total, rt) => total + (beds_by_room[rt.name] || []).length, 0)

            return `
                <details class="floor_block" data-floor="${floor_name}" open>
                    <summary class="floor_title">
                        ${frappe.utils.escape_html(floor_name)}
                        <span class="floor_count">${room_types_in_floor.length} room${room_types_in_floor.length === 1 ? '' : 's'} &middot; ${bed_count} bed${bed_count === 1 ? '' : 's'}</span>
                    </summary>
                    <div class="floor_content">
                        ${rooms_html}
                    </div>
                </details>
            `
        }).join('')

        $('#room').empty()
        $(floors_html).appendTo('#room')
    },

    render_room_block: function (rt, floor_name, beds) {
        let me = this
        let bed_cards = beds.map(bed => me.render_bed_card(bed, rt, floor_name)).join('')
        let rate_html = rt.rate ? `<span class="room_rate">Rate: ${format_currency(rt.rate)}</span>` : ''
        let ward_html = rt.ward ? `<span class="room_ward">${frappe.utils.escape_html(rt.ward)}</span>` : ''

        return `
            <div class="room_block" data-room="${rt.name}">
                <h3 class="room_title">
                    ${frappe.utils.escape_html(rt.name)}
                    ${ward_html}
                    ${rate_html}
                </h3>
                <div class="my_main_cards">
                    ${bed_cards || '<div class="no_results">No beds in this room.</div>'}
                </div>
            </div>
        `
    },

    render_bed_card: function (bed, rt, floor_name) {
        let sts_bg_class = "card_one_occupied"
        let btn = ``

        if (bed.occupancy_status === "Occupied") {
            sts_bg_class = "card_one_occupied"
        } else if (bed.occupancy_status === "Vacant") {
            sts_bg_class = "card_one_vocant"
        } else if (bed.occupancy_status === "In Cleaning") {
            sts_bg_class = "card_one_cleaning"
            btn = `<button class="btn btn-warning mb-3" style="color:white" onclick="ready('${bed.name}')">Ready</button>`
        } else if (bed.occupancy_status === "Discharge Ordered") {
            sts_bg_class = "card_discharge"
        }

        let searchable = {
            bed: (bed.name || '').toLowerCase(),
            room: (rt.name || '').toLowerCase(),
            floor: (floor_name || '').toLowerCase(),
            rate: (rt.rate || '').toString().toLowerCase(),
            ward: (rt.ward || '').toString().toLowerCase(),
        }

        return `
            <div class="${sts_bg_class} bed_card"
                data-bed="${searchable.bed}"
                data-room="${searchable.room}"
                data-floor="${searchable.floor}"
                data-rate="${searchable.rate}"
                data-ward="${searchable.ward}">
                <div class="bed_icon"><span><i class="fa fa-bed"></i></span></div>
                <span class="bed_tex">${frappe.utils.escape_html(bed.name)}</span>
                <span class="bed_tex bed_floor">${frappe.utils.escape_html(floor_name)}</span>
                <span class="bed_tex">${frappe.utils.escape_html(bed.occupancy_status || "")}</span>
                <span class="bed_tex">${frappe.utils.escape_html(bed.patient || "")}</span>
                ${btn}
            </div>
        `
    },
})

function ready(bed) {
    frappe.call({
        method: "his.api.ward_management.ready", // dotted path to server method
        args: {
            "bed": bed
        },
        callback: function (r) {
            frappe.utils.play_sound("submit")
            frappe.show_alert({
                message: __('Bed: ' + bed + " Vacanted Successfully!!"),
                indicator: 'green',
            }, 5);
            location.reload();
        }
    });
}