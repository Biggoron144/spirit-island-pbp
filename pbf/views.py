import json
import itertools
from random import sample, shuffle

from django.db import transaction
@@ -413,23 +414,167 @@ def add_player(request, game_id):
    except Exception as ex:
        print(ex)
        pass
    gp.hand.set(Card.objects.filter(spirit=spirit))

    make_initial_hand(gp)

    return redirect(reverse('game_setup', args=[game.id]))

def make_initial_hand(gp, remove_from_decks=True):
    game = gp.game
    gp.hand.set(Card.objects.filter(spirit=gp.spirit))
    if gp.full_name() in spirit_additional_cards:
        additional_starting_cards = spirit_additional_cards[gp.full_name()]
        for card_name in additional_starting_cards:
            card = Card.objects.get(name=card_name)
            gp.hand.add(card)
            if card.type == Card.MINOR:
                game.minor_deck.remove(card)
            elif card.type == Card.MAJOR:
                game.major_deck.remove(card)
            if remove_from_decks:
                if card.type == Card.MINOR:
                    game.minor_deck.remove(card)
                elif card.type == Card.MAJOR:
                    game.major_deck.remove(card)
    if gp.full_name() in spirit_remove_cards:
        remove_cards = spirit_remove_cards[gp.full_name()]
        for card_name in remove_cards:
            card = Card.objects.get(name=card_name)
            card = gp.hand.remove(card)

    return redirect(reverse('game_setup', args=[game.id]))
def import_game(request):
    def cards_with_name(cards):
        # Cards can be specified as either:
        # - just their name as a string
        # - or a dict with key "name"
        # (it is an error to provide something other than a string or dict)
        names = {(card if isinstance(card, str) else card['name']) for card in cards}
        exact_name_matches = Card.objects.filter(name__in=names)
        if exact_name_matches.count() == len(cards):
            return exact_name_matches

        # TODO: Is there a way to do a case-insensitive match on a set?
        # maybe not: https://stackoverflow.com/questions/2667524/django-query-case-insensitive-list-match
        remaining_names_needed = names - {card.name for card in exact_name_matches}
        remaining_cards = [Card.objects.get(name__iexact=name) for name in remaining_names_needed]
        if len(remaining_cards) == len(remaining_names_needed):
            return list(exact_name_matches) + remaining_cards
        # TODO: This feedback needs to be shown in UI
        still_not_matched = remaining_names_needed - {card.name for card in remaining_cards}
        raise ValueError(f"Couldn't find cards {still_not_matched}")

    # The general strategy of the importer is that it will allow most fields to be optional,
    # using a reasonable default for any field not defined.
    #
    # Really, this should be unnecessary for games that were exported from the API,
    # as they should have all the fields,
    # but it doesn't seem to hurt to be permissive here.

    to_import = json.load(request.FILES['json'])
    game = Game(
            name=to_import.get('name', 'Untitled Imported Game'),
            scenario=to_import.get('scenario', ''),
            )
    # we are not importing the discord_channel,
    # because it's not yet been proven to be desirable to automatically do this.
    game.save()

    if 'discard_pile' in to_import:
        game.discard_pile.set(discards := cards_with_name(to_import['discard_pile']))
        cards_in_game = {card.id for card in discards}
    else:
        cards_in_game = set()

    # set minor/major decks after we've imported players,
    # because we may want to exclude cards players are holding.

    # if any player doesn't have colour defined,
    # we need to assign an unused colour,
    # not duplicating a player that does have a colour defined
    colours = {color for (color, _) in GamePlayer.COLORS}
    used_colours = {player.get('color', '') for player in to_import.get('players', [])}
    available_colours = colours - used_colours
    if not available_colours:
        available_colours = colours

    for player in to_import.get('players', []):
        elts = (temp_or_perm + "_" + elt for temp_or_perm in ("temporary", "permanent") for elt in ("sun", "moon", "fire", "air", "water", "earth", "plant", "animal"))
        # if these basic attributes aren't set, we'll rely on the database defaults
        basic_attrs = {attr: player[attr] for attr in (
            'name', 'aspect', 'energy',
            'ready', 'paid_this_turn', 'gained_this_turn',
            'spirit_specific_resource', 'spirit_specific_per_turn_flags',
            *elts,
            ) if attr in player}

        # Spirit can be specified as either:
        # - just their name as a string
        # - or a dict with key "name"
        # Error if they don't have a spirit defined.
        spirit_name = player['spirit'] if isinstance(player['spirit'], str) else player['spirit']['name']
        gp = GamePlayer(
                game=game,
                **basic_attrs,
                color=player.get('color', next(iter(available_colours))),
                spirit=Spirit.objects.get(name__iexact=spirit_name),
                starting_energy=spirit_base_energy_per_turn[spirit_name],
                )
        if gp.color in available_colours:
            available_colours.remove(gp.color)
            # if there are no colours left, we'll just have to repopulate.
            if not available_colours:
                available_colours = {color for (color, _) in GamePlayer.COLORS}
        gp.save()

        for (i, (expected_presence, import_presence)) in enumerate(zip(spirit_presence[spirit_name], itertools.chain(player.get('presence', []), itertools.repeat(None)))):
            expected_energy = expected_presence[3] if 3 < len(expected_presence) else ''
            expected_elements = expected_presence[4] if 4 < len(expected_presence) else ''

            if import_presence:
                # limitation: This will cause the import to fail if we change the order of spirits' presences.
                # maybe it's better to also import the top/left coordinates.
                # we aren't doing this yet because the API doesn't export them.
                if import_presence['energy'] != expected_energy:
                    raise ValueError(f"presence at {expected_presence[0]}, {expected_presence[1]} should have {expected_energy} energy but had {import_presence['energy']}")
                if import_presence['elements'] != expected_elements:
                    raise ValueError(f"presence at {expected_presence[0]}, {expected_presence[1]} should have {expected_elements} elements but had {import_presence['elements']}")
                gp.presence_set.create(left=expected_presence[0], top=expected_presence[1], opacity=import_presence['opacity'], energy=import_presence['energy'], elements=import_presence['elements'])
            else:
                expected_opacity = 0.0 if gp.aspect == 'Locus' and i == 0 else expected_presence[2]
                gp.presence_set.create(left=expected_presence[0], top=expected_presence[1], opacity=expected_opacity, energy=expected_energy, elements=expected_elements)

        if 'hand' in player:
            gp.hand.set(hand := cards_with_name(player['hand']))
            cards_in_game |= {card.id for card in hand}
        else:
            # We haven't made the major/minor decks yet
            # (because we need to know what cards to exclude from it)
            # so we should not remove cards from it yet,
            # only record the cards so that we remove them when the decks are made.
            make_initial_hand(gp, remove_from_decks=False)
            if gp.full_name() in spirit_additional_cards:
                cards_in_game |= {Card.objects.get(name=card).id for card in spirit_additional_cards[gp.full_name()]}

        for name in ('discard', 'play', 'selection', 'days', 'healing'):
            if name in player:
                getattr(gp, name).set(cards := cards_with_name(player[name]))
                cards_in_game |= {card.id for card in cards}
        if 'impending' in player:
            for impending in player['impending']:
                card = Card.objects.get(name__iexact=impending['card'] if isinstance(impending['card'], str) else impending['card']['name'])
                GamePlayerImpendingWithEnergy(
                        gameplayer=gp,
                        card=card,
                        **{attr: impending[attr] for attr in ('in_play', 'energy', 'this_turn') if attr in impending},
                        ).save()
                cards_in_game.add(card.id)

    for (name, type) in (('minor_deck', Card.MINOR), ('major_deck', Card.MAJOR)):
        deck = getattr(game, name)
        if name in to_import:
            deck.set(cards_with_name(to_import[name]))
        else:
            # if someone imports a discard pile and not a major/minor deck,
            # exclude discarded cards and cards being held by any player
            deck.set(Card.objects.filter(type=type).exclude(id__in=cards_in_game))

    return redirect(reverse('view_game', args=[game.id]))

def view_game(request, game_id, spirit_spec=None):
    game = get_object_or_404(Game, pk=game_id)
    if request.method == 'POST':
        if 'spirit_spec' in request.POST:
            spirit_spec = request.POST['spirit_spec']
        if 'screenshot' in request.FILES:
            form = GameForm(request.POST, request.FILES, instance=game)
            if form.is_valid():
                form.save()
                add_log_msg(game, text=f'New screenshot uploaded.', images='.' + game.screenshot.url)
                return redirect(reverse('view_game', args=[game.id, spirit_spec] if spirit_spec else [game.id]))
        if 'screenshot2' in request.FILES:
            form = GameForm2(request.POST, request.FILES, instance=game)
            if form.is_valid():
                form.save()
                add_log_msg(game, text=f'New screenshot uploaded.', images='.' + game.screenshot2.url)
                return redirect(reverse('view_game', args=[game.id, spirit_spec] if spirit_spec else [game.id]))

    tab_id = try_match_spirit(game, spirit_spec) or (game.gameplayer_set.first().id if game.gameplayer_set.exists() else None)
    logs = reversed(game.gamelog_set.order_by('-date').all()[:30])
    return render(request, 'game.html', { 'game': game, 'logs': logs, 'tab_id': tab_id, 'spirit_spec': spirit_spec })

def try_match_spirit(game, spirit_spec):
    if not spirit_spec:
        return None

    if spirit_spec.isnumeric():
        spirit_spec = int(spirit_spec)
        player_ids = game.gameplayer_set.values_list('id', flat=True)
        if 1 <= spirit_spec <= len(player_ids):
            return player_ids[spirit_spec - 1]
        elif spirit_spec in player_ids:
            return spirit_spec
    else:
        aspect_match = game.gameplayer_set.filter(aspect__iexact=spirit_spec)
        if aspect_match.exists():
            return aspect_match.first().id
        # prefer the base spirit if they search for a spirit name,
        # in case there is one base and one aspected spirit in the same game.
        base_spirit_match = game.gameplayer_set.filter(spirit__name__iexact=spirit_spec, aspect=None)
        if base_spirit_match.exists():
            return base_spirit_match.first().id
        spirit_match = game.gameplayer_set.filter(spirit__name__iexact=spirit_spec)
        if spirit_match.exists():
            return spirit_match.first().id

        # look for an exact match first, in case someone's name is a substring of another
        # on the other hand, if someone's name is exactly a spirit or aspect's name, not much we can do!
        player_exact_match = game.gameplayer_set.filter(name__iexact=spirit_spec)
        if player_exact_match.exists():
            return player_exact_match.first().id
        player_match = game.gameplayer_set.filter(name__icontains=spirit_spec)
        if player_match.exists():
            return player_match.first().id

def draw_cards(request, game_id):
    game = get_object_or_404(Game, pk=game_id)
    cards_needed = int(request.POST['num_cards'])
    type = request.POST['type']
    if cards_needed <= 0:
        return render(request, 'host_draw.html', {'msg': f"Can't draw {cards_needed} cards"})

    cards_drawn = cards_from_deck(game, cards_needed, type)
    game.discard_pile.add(*cards_drawn)

    draw_result = f"drew {len(cards_drawn)} {type} power card{'s' if len(cards_drawn) != 1 else ''}"
    draw_result_explain = "" if len(cards_drawn) == cards_needed else f" (there were not enough cards to draw all {cards_needed})"
    card_names = ', '.join(card.name for card in cards_drawn)

    add_log_msg(game, text=f'Host {draw_result}: {card_names}', images=",".join('./pbf/static' + card.url() for card in cards_drawn))

    return render(request, 'host_draw.html', {'msg': f"You {draw_result}{draw_result_explain}: {card_names}", 'cards': cards_drawn})

def cards_from_deck(game, cards_needed, type):
    if type == 'minor':
        deck = game.minor_deck
    elif type == 'major':
        deck = game.major_deck
    else:
        raise ValueError(f"can't draw from {type} deck")

    cards_have = deck.count()

    if cards_have >= cards_needed:
        cards_drawn = sample(list(deck.all()), cards_needed)
        deck.remove(*cards_drawn)
    else:
        # reshuffle needed, but first draw all the cards we do have
        cards_drawn = list(deck.all())
        cards_remain = cards_needed - cards_have
        deck.clear()
        reshuffle_discard(game, type)
        if deck.count() >= cards_remain:
            new_cards = sample(list(deck.all()), cards_remain)
            cards_drawn.extend(new_cards)
            deck.remove(*new_cards)
        else:
            cards_drawn.extend(list(deck.all()))
            deck.clear()

    return cards_drawn

def reshuffle_discard(game, type):
    if type == 'minor':
        minors = game.discard_pile.filter(type=Card.MINOR).all()
        for card in minors:
            game.discard_pile.remove(card)
            game.minor_deck.add(card)
    else:
        majors = game.discard_pile.filter(type=Card.MAJOR).all()
        for card in majors:
            game.discard_pile.remove(card)
            game.major_deck.add(card)

    add_log_msg(game, text=f'Re-shuffling {type} power deck')

def take_powers(request, player_id, type, num):
    player = get_object_or_404(GamePlayer, pk=player_id)

    taken_cards = cards_from_deck(player.game, num, type)
    player.hand.add(*taken_cards)

    if num == 1:
        card = taken_cards[0]
        add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} takes a {type} power: {card.name}', images='./pbf/static' + card.url())
    else:
        # There's a bit of tension between the function's name/functionality and game terminology.
        #
        # As used in code, take_powers is being used when we don't go through the selection process
        # (the spirit gets all the cards directly into their hand).
        # It's natural to use this for Mentor Shifting Memory of Ages,
        # since the number of cards they get to keep is equal to the number of cards they look at.
        #
        # However, we do want to use the word "gain" in the log message, not "take",
        # because Mentor still needs to forget a power card.
        #
        # The alternative is to special-case gain_power to not use selection if it's Mentor and num == 2.
        # Either way we have to make some special cases,
        # and doing it here at least matches in mechanism better.
        card_names = ', '.join(card.name for card in taken_cards)
        add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} gains {num} {type} powers: {card_names}', images=",".join('./pbf/static' + card.url() for card in taken_cards))

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player, 'taken_cards': taken_cards}))

def gain_healing(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    selection = [
            Card.objects.get(name="Serene Waters"),
            Card.objects.get(name="Waters Renew"),
            Card.objects.get(name="Roiling Waters"),
            Card.objects.get(name="Waters Taste of Ruin")
            ]

    player.selection.set(selection)

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def gain_power(request, player_id, type, num):
    player = get_object_or_404(GamePlayer, pk=player_id)

    selection = cards_from_deck(player.game, num, type)
    player.selection.set(selection)

    cards_str = ", ".join([str(card) for card in selection])
    images = ",".join(['./pbf/static' + card.url() for card in selection])
    add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} gains a {type} power. Choices: {cards_str}',
            images=images)

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def minor_deck(request, game_id):
    game = get_object_or_404(Game, pk=game_id)
    return render(request, 'power_deck.html', {'name': 'Minor', 'cards': game.minor_deck.all()})

def major_deck(request, game_id):
    game = get_object_or_404(Game, pk=game_id)
    return render(request, 'power_deck.html', {'name': 'Major', 'cards': game.major_deck.all()})

def discard_pile(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    return render(request, 'discard_pile.html', { 'player': player })

def return_to_deck(request, player_id, card_id):
    # this doesn't actually manipulate the player in any way,
    # except to return to their tab after the operation is done
    player = get_object_or_404(GamePlayer, pk=player_id)
    game = player.game
    card = get_object_or_404(game.discard_pile, pk=card_id)
    game.discard_pile.remove(card)

    if card.type == card.MINOR:
        game.minor_deck.add(card)
    elif card.type == card.MAJOR:
        game.major_deck.add(card)
    else:
        raise ValueError(f"Can't return {card}")

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def choose_from_discard(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.game.discard_pile, pk=card_id)
    player.hand.add(card)
    player.game.discard_pile.remove(card)

    add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} takes {card.name} from the power discard pile')

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def send_days(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    for location in [player.selection, player.game.discard_pile]:
        try:
            card = get_object_or_404(location, pk=card_id)
            player.days.add(card)
            location.remove(card)
            break
        except:
            pass

    add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} sends {card.name} to the Days That Never Were')

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def choose_card(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.selection, pk=card_id)

    if card.is_healing():
        return choose_healing_card(request, player, card)

    player.hand.add(card)
    player.selection.remove(card)
    # if there are 5 minor cards left in their selection,
    # we assume this was a Boon of Reimagining (draw 6 and gain 2)
    # so we do not send the cards to the discard in that case.
    # Also, Boon of Reimagining on Mentor Shifting Memory of Ages will draw 4 and gain 3.
    # Otherwise, we do discard the cards.
    # (It has to be minors because Covets Gleaming Shards of Earth can draw 6 majors)
    #
    # For now it works to make this decision solely based on the number of cards drawn.
    # If there's ever another effect that does draw 6 gain N with N != 2,
    # we would have to redo this in some way,
    # perhaps by adding a field to GamePlayer indicating the number of cards that are to be gained.
    cards_left = player.selection.count()
    can_keep_selecting = card.type == Card.MINOR and (cards_left == 5 or player.aspect == 'Mentor' and cards_left > 1)
    if not can_keep_selecting:
        for discard in player.selection.all():
            player.game.discard_pile.add(discard)
        player.selection.clear()

    add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} gains {card.name}')

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def undo_gain_card(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    game = player.game

    to_remove = []
    for sel in player.selection.all():
        if sel.type == Card.MINOR:
            game.minor_deck.add(sel)
            # we don't remove from player.selection immediately,
            # as that would modify the selection we're iterating over.
            to_remove.append(sel)
        elif sel.type == Card.MAJOR:
            game.major_deck.add(sel)
            to_remove.append(sel)
        elif sel.is_healing():
            to_remove.append(sel)
        # If it's not any of these types, we'll leave it in selection, as something's gone wrong.

    for rem in to_remove:
        player.selection.remove(rem)

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def choose_healing_card(request, player, card):
    if card.name.startswith('Waters'):
        player.healing.remove(player.healing.filter(name__startswith='Waters').first())
    else:
        player.healing.clear()
    player.healing.add(card)
    player.selection.clear()

    add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} claims {card.name}')

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def choose_days(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.days, pk=card_id)
    player.hand.add(card)
    player.days.remove(card)

    add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} gains {card.name} from the Days That Never Were')

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def create_days(request, player_id, num):
    player = get_object_or_404(GamePlayer, pk=player_id)
    game = player.game

    for deck in [game.minor_deck, game.major_deck]:
        cards = list(deck.all())
        shuffle(cards)
        for c in cards[:num]:
            deck.remove(c)
            player.days.add(c)

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def compute_card_thresholds(player):
    equiv_elements = player.equiv_elements()
    player.play_cards = []
    for card in player.play.all():
        card.computed_thresholds = card.thresholds(player.elements, equiv_elements)
        player.play_cards.append(card)
    player.hand_cards = []
    for card in player.hand.all():
        card.computed_thresholds = card.thresholds(player.elements, equiv_elements)
        player.hand_cards.append(card)
    player.selection_cards = []
    for card in player.selection.all():
        card.computed_thresholds = card.thresholds(player.elements, equiv_elements)
        if card.is_healing():
            card.computed_thresholds.extend(card.healing_thresholds(player.healing.count(), player.spirit_specific_resource_elements()))
        player.selection_cards.append(card)
    # we could just unconditionally set this, but I guess we'll save a database query if they're not Dances Up Earthquakes.
    player.computed_impending = player.gameplayerimpendingwithenergy_set.all() if player.spirit.name == 'Earthquakes' else []
    for imp in player.computed_impending:
        imp.card.computed_thresholds = imp.card.thresholds(player.elements, equiv_elements)

def gain_energy_on_impending(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    to_gain = player.impending_energy()
    for impending in player.gameplayerimpendingwithenergy_set.all():
        if impending.this_turn:
            # You only gain energy on cards made impending on previous turns.
            continue
        # Let's cap the energy at the cost of the card.
        # There's no real harm in letting it exceed the cost
        # (the UI will still let you play it),
        # it's just that undoing it will require extra clicks on the -1.
        impending.energy += to_gain
        if impending.energy >= impending.cost_with_scenario:
            impending.energy = impending.cost_with_scenario
            impending.in_play = True
        impending.save()
    player.spirit_specific_per_turn_flags |= GamePlayer.SPIRIT_SPECIFIC_INCREMENTED_THIS_TURN
    player.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def impend_card(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.hand, pk=card_id)
    player.impending_with_energy.add(card)
    player.hand.remove(card)

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def unimpend_card(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.impending_with_energy, pk=card_id)
    player.impending_with_energy.remove(card)
    player.hand.add(card)

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def add_energy_to_impending(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.impending_with_energy, pk=card_id)
    impending_with_energy = get_object_or_404(GamePlayerImpendingWithEnergy, gameplayer=player, card=card)
    if not impending_with_energy.in_play and impending_with_energy.energy < impending_with_energy.cost_with_scenario:
        impending_with_energy.energy += 1
        impending_with_energy.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def remove_energy_from_impending(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.impending_with_energy, pk=card_id)
    impending_with_energy = get_object_or_404(GamePlayerImpendingWithEnergy, gameplayer=player, card=card)
    if not impending_with_energy.in_play and impending_with_energy.energy > 0:
        impending_with_energy.energy -= 1
        impending_with_energy.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def play_from_impending(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.impending_with_energy, pk=card_id)
    impending_with_energy = get_object_or_404(GamePlayerImpendingWithEnergy, gameplayer=player, card=card)
    if not impending_with_energy.in_play and impending_with_energy.energy >= impending_with_energy.cost_with_scenario:
        impending_with_energy.in_play = True
        impending_with_energy.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def unplay_from_impending(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.impending_with_energy, pk=card_id)
    impending_with_energy = get_object_or_404(GamePlayerImpendingWithEnergy, gameplayer=player, card=card)
    if impending_with_energy.in_play:
        impending_with_energy.in_play = False
        impending_with_energy.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def play_card(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.hand, pk=card_id)
    player.play.add(card)
    player.hand.remove(card)

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def unplay_card(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.play, pk=card_id)
    player.hand.add(card)
    player.play.remove(card)

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def forget_card(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)

    for location in [player.hand, player.play, player.discard, player.impending_with_energy]:
        try:
            card = location.get(pk=card_id)
            location.remove(card)
            player.game.discard_pile.add(card)
            break
        except:
            pass

    add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} forgets {card.name}')

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))


def reclaim_card(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    card = get_object_or_404(player.discard, pk=card_id)
    player.hand.add(card)
    player.discard.remove(card)

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def reclaim_all(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    cards = list(player.discard.all())
    for card in cards:
        player.hand.add(card)
    player.discard.clear()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def discard_all(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    cards = list(player.play.all())
    for card in cards:
        player.discard.add(card)

    if player.spirit.name == 'Earthquakes':
        played_impending = GamePlayerImpendingWithEnergy.objects.filter(gameplayer=player, in_play=True)
        for i in played_impending.all():
            player.discard.add(i.card)
        played_impending.delete()
        player.gameplayerimpendingwithenergy_set.update(this_turn=False)

    player.play.clear()
    player.ready = False
    player.gained_this_turn = False
    player.paid_this_turn = False
    player.temporary_sun = 0
    player.temporary_moon = 0
    player.temporary_fire = 0
    player.temporary_air = 0
    player.temporary_water = 0
    player.temporary_earth = 0
    player.temporary_plant = 0
    player.temporary_animal = 0
    player.spirit_specific_per_turn_flags = 0
    player.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def discard_card(request, player_id, card_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    try:
        card = player.play.get(pk=card_id)
        player.discard.add(card)
        player.play.remove(card)
    except:
        pass
    try:
        card = player.hand.get(pk=card_id)
        player.discard.add(card)
        player.hand.remove(card)
    except:
        pass

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def ready(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    player.ready = not player.ready
    player.save()

    if player.ready:
        if player.gained_this_turn:
            add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} gains {player.get_gain_energy()} energy')
        for card in player.play.all():
            add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} plays {card.name}')
        if player.paid_this_turn:
            add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} pays {player.get_play_cost()} energy')
        add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} is ready')
    else:
        add_log_msg(player.game, text=f'{player.circle_emoji} {player.spirit.name} is not ready')

    if player.game.gameplayer_set.filter(ready=False).count() == 0:
        add_log_msg(player.game, text=f'All spirits are ready!')

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def unready(request, game_id):
    game = get_object_or_404(Game, pk=game_id)
    for player in game.gameplayer_set.all():
        player.ready = False
        player.save()

    add_log_msg(player.game, text=f'All spirits marked not ready')

    return redirect(reverse('view_game', args=[game.id]))

def time_passes(request, game_id):
    game = get_object_or_404(Game, pk=game_id)
    for player in game.gameplayer_set.all():
        player.ready = False
        player.save()
    game.turn += 1
    game.save()

    add_log_msg(player.game, text=f'Time passes...')
    add_log_msg(player.game, text=f'-- Turn {game.turn} --')

    return redirect(reverse('view_game', args=[game.id]))


def change_energy(request, player_id, amount):
    amount = int(amount)
    player = get_object_or_404(GamePlayer, pk=player_id)
    player.energy += amount
    player.save()

    return with_log_trigger(render(request, 'energy.html', {'player': player}))

def pay_energy(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    amount = player.get_play_cost()
    player.energy -= amount
    player.paid_this_turn = True
    player.save()

    return with_log_trigger(render(request, 'energy.html', {'player': player}))

def gain_energy(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    amount = player.get_gain_energy()
    player.energy += amount
    player.gained_this_turn = True
    player.save()

    return with_log_trigger(render(request, 'energy.html', {'player': player}))

def change_spirit_specific_resource(request, player_id, amount):
    amount = int(amount)
    player = get_object_or_404(GamePlayer, pk=player_id)
    player.spirit_specific_resource += amount
    if amount > 0:
        player.spirit_specific_per_turn_flags |= GamePlayer.SPIRIT_SPECIFIC_INCREMENTED_THIS_TURN
    elif amount < 0:
        player.spirit_specific_per_turn_flags |= GamePlayer.SPIRIT_SPECIFIC_DECREMENTED_THIS_TURN
    player.save()

    # The spirit-specific resource is displayed in energy.html,
    # because some of them can change simultaneously with energy (e.g. Rot).
    return with_log_trigger(render(request, 'energy.html', {'player': player}))

def gain_rot(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    player.spirit_specific_resource += player.rot_gain()
    player.spirit_specific_per_turn_flags |= GamePlayer.ROT_GAINED_THIS_TURN
    player.save()

    return with_log_trigger(render(request, 'energy.html', {'player': player}))

def convert_rot(request, player_id):
    player = get_object_or_404(GamePlayer, pk=player_id)
    # be sure to change energy before rot,
    # because energy gain is based on rot.
    player.energy += player.energy_from_rot()
    player.spirit_specific_resource -= player.rot_loss()
    player.spirit_specific_per_turn_flags |= GamePlayer.ROT_CONVERTED_THIS_TURN
    player.save()

    return with_log_trigger(render(request, 'energy.html', {'player': player}))

def toggle_presence(request, player_id, left, top):
    player = get_object_or_404(GamePlayer, pk=player_id)
    presence = get_object_or_404(player.presence_set, left=left, top=top)
    presence.opacity = abs(1.0 - presence.opacity)
    presence.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def add_element(request, player_id, element):
    player = get_object_or_404(GamePlayer, pk=player_id)
    if element == 'sun': player.temporary_sun += 1
    if element == 'moon': player.temporary_moon += 1
    if element == 'fire': player.temporary_fire += 1
    if element == 'air': player.temporary_air += 1
    if element == 'water': player.temporary_water += 1
    if element == 'earth': player.temporary_earth += 1
    if element == 'plant': player.temporary_plant += 1
    if element == 'animal': player.temporary_animal += 1
    player.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def remove_element(request, player_id, element):
    player = get_object_or_404(GamePlayer, pk=player_id)
    if element == 'sun': player.temporary_sun -= 1
    if element == 'moon': player.temporary_moon -= 1
    if element == 'fire': player.temporary_fire -= 1
    if element == 'air': player.temporary_air -= 1
    if element == 'water': player.temporary_water -= 1
    if element == 'earth': player.temporary_earth -= 1
    if element == 'plant': player.temporary_plant -= 1
    if element == 'animal': player.temporary_animal -= 1
    player.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def add_element_permanent(request, player_id, element):
    player = get_object_or_404(GamePlayer, pk=player_id)
    if element == 'sun': player.permanent_sun += 1
    if element == 'moon': player.permanent_moon += 1
    if element == 'fire': player.permanent_fire += 1
    if element == 'air': player.permanent_air += 1
    if element == 'water': player.permanent_water += 1
    if element == 'earth': player.permanent_earth += 1
    if element == 'plant': player.permanent_plant += 1
    if element == 'animal': player.permanent_animal += 1
    player.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def remove_element_permanent(request, player_id, element):
    player = get_object_or_404(GamePlayer, pk=player_id)
    if element == 'sun': player.permanent_sun -= 1
    if element == 'moon': player.permanent_moon -= 1
    if element == 'fire': player.permanent_fire -= 1
    if element == 'air': player.permanent_air -= 1
    if element == 'water': player.permanent_water -= 1
    if element == 'earth': player.permanent_earth -= 1
    if element == 'plant': player.permanent_plant -= 1
    if element == 'animal': player.permanent_animal -= 1
    player.save()

    compute_card_thresholds(player)
    return with_log_trigger(render(request, 'player.html', {'player': player}))

def tab(request, game_id, player_id):
    game = get_object_or_404(Game, pk=game_id)
    player = get_object_or_404(GamePlayer, pk=player_id)
    compute_card_thresholds(player)
    return render(request, 'tabs.html', {'game': game, 'player': player})

def game_logs(request, game_id):
    game = get_object_or_404(Game, pk=game_id)
    logs = reversed(game.gamelog_set.order_by('-date').all()[:30])
    return render(request, 'logs.html', {'game': game, 'logs': logs})

