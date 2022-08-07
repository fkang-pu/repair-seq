from collections import defaultdict

import hits.visualize
from hits import interval, sam
from hits.utilities import memoized_property

import knock_knock.visualize
import knock_knock.pegRNAs
from knock_knock.outcome import *

import repair_seq.prime_editing_layout

class Layout(repair_seq.prime_editing_layout.Layout):
    category_order = [
        ('wild type',
            ('clean',
             'mismatches',
             'short indel far from cut',
            ),
        ),
        ('intended edit',
            ('replacement',
             'partial replacement',
             'deletion',
            ),
        ),
        ('unintended annealing of RT\'ed sequence',
            ('left pegRNA',
             'right pegRNA',
             'both pegRNAs',
            ),
        ),
        ('flipped pegRNA incorporation',
            ('left pegRNA',
             'right pegRNA',
             'both pegRNAs',
            ),
        ),
        ('deletion',
            ('clean',
             'mismatches',
             'multiple',
            ),
        ),
        ('duplication',
            ('simple',
             'iterated',
             'complex',
            ),
        ),
        ('insertion',
            ('clean',
             'mismatches',
            ),
        ),
        ('extension from intended annealing',
            ('n/a',
            ),
        ),
        ('genomic insertion',
            ('hg19',
             'bosTau7',
             'e_coli',
            ),
        ),
        ('uncategorized',
            ('uncategorized',
            ),
        ),
        ('nonspecific amplification',
            ('hg19',
             'hg38',
             'bosTau7',
             'e_coli',
             'primer dimer',
             'unknown',
            ),
        ),
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.ins_size_to_split_at = 1
        self.categorized = False

    @memoized_property
    def parsimonious_split_target_and_donor_alignments(self):
        return interval.make_parsimonious(self.split_target_and_donor_alignments)

    @memoized_property
    def has_any_pegRNA_extension_al(self):
        return {side for side in ['left', 'right'] if self.pegRNA_extension_als[side] is not None}

    @memoized_property
    def has_intended_pegRNA_overlap(self):
        als = self.pegRNA_extension_als

        if 'left' in als and 'right' in als:
            share_overlap = self.share_feature(als['left'], 'overlap', als['right'], 'overlap')
        else:
            share_overlap = False

        return share_overlap

    @memoized_property
    def intended_SNVs_replaced(self):
        als = self.pegRNA_extension_als
        positions_not_replaced = {side: self.alignment_SNV_summary(als[side])['mismatches'] for side in als}
        positions_replaced = {side: self.alignment_SNV_summary(als[side])['matches'] for side in als}

        any_positions_not_replaced = any(len(ps) > 0 for side, ps in positions_not_replaced.items())
        any_positions_replaced = any(len(ps) > 0 for side, ps in positions_replaced.items())

        if not any_positions_replaced:
            fraction_replaced = 'none'
        else:
            if any_positions_not_replaced:
                fraction_replaced = 'partial replacement'
            else:
                fraction_replaced = 'replacement'

        return fraction_replaced

    @memoized_property
    def is_intended_replacement(self):
        if self.target_info.pegRNA_intended_deletion is not None:
            status = False
        else:
            if not self.has_intended_pegRNA_overlap:
                status = False
            else:
                if self.intended_SNVs_replaced == 'none':
                    status = False
                else:
                    status = self.intended_SNVs_replaced

        return status

    @memoized_property
    def has_any_flipped_pegRNA_al(self):
        return {side for side in ['left', 'right'] if len(self.flipped_pegRNA_als[side]) > 0}

    def alignment_SNV_summary(self, al):
        ''' Identifies any positions in al that correspond to sequence differences
        between the target and pegRNAs and separates them based on whether they
        agree with al's reference sequence or not.
        ''' 

        ti = self.target_info
        SNPs = ti.pegRNA_SNVs
        
        positions_seen = {
            'matches': set(),
            'mismatches': set(),
        }

        if al is None or al.is_unmapped:
            return positions_seen

        ref_seq = ti.reference_sequences[al.reference_name]

        pegRNA_SNP_positions = {SNPs[al.reference_name][name]['position'] for name in SNPs[al.reference_name]}

        for true_read_i, read_b, ref_i, ref_b, qual in sam.aligned_tuples(al, ref_seq):
            # Note: read_b and ref_b are as if the read is the forward strand
            if ref_i in pegRNA_SNP_positions:
                if read_b != ref_b:
                    positions_seen['mismatches'].add(ref_i)
                else:
                    positions_seen['matches'].add(ref_i)

        return positions_seen

    def categorize(self):
        self.outcome = None

        if len(self.seq) <= self.target_info.combined_primer_length + 10:
            self.category = 'nonspecific amplification'

            if self.non_primer_nts <= 2:
                self.subcategory = 'primer dimer'
            else:
                self.subcategory = 'unknown'

            self.details = 'n/a'
            self.relevant_alignments = self.uncategorized_relevant_alignments

        elif self.no_alignments_detected:
            self.category = 'uncategorized'
            self.subcategory = 'uncategorized'
            self.details = 'n/a'
            self.outcome = None

        elif self.single_read_covering_target_alignment:
            target_alignment = self.single_read_covering_target_alignment
            interesting_indels, uninteresting_indels = self.interesting_and_uninteresting_indels([target_alignment])

            if len(interesting_indels) == 0:
                if self.starts_at_expected_location:
                    # Need to check in case the intended replacements only involves minimal changes. 
                    if self.is_intended_replacement:
                        self.category = 'intended edit'
                        self.subcategory = self.is_intended_replacement
                        self.outcome = Outcome('n/a')
                        self.relevant_alignments = self.target_edge_alignments_list + self.pegRNA_extension_als_list

                    else:
                        self.category = 'wild type'

                        if len(self.non_pegRNA_SNVs) == 0 and len(uninteresting_indels) == 0:
                            self.subcategory = 'clean'
                            self.outcome = Outcome('n/a')

                        elif len(uninteresting_indels) == 1:
                            self.subcategory = 'short indel far from cut'

                            indel = uninteresting_indels[0]
                            if indel.kind == 'D':
                                self.outcome = DeletionOutcome(indel)
                            elif indel.kind == 'I':
                                self.outcome = InsertionOutcome(indel)
                            else:
                                raise ValueError(indel.kind)

                        elif len(uninteresting_indels) > 1:
                            self.category = 'uncategorized'
                            self.subcategory = 'uncategorized'
                            self.outcome = Outcome('n/a')

                        else:
                            self.subcategory = 'mismatches'
                            self.outcome = MismatchOutcome(self.non_pegRNA_SNVs)

                        self.relevant_alignments = [target_alignment]

                else:
                    self.category = 'uncategorized'
                    self.subcategory = 'uncategorized'
                    self.outcome = Outcome('n/a')

                    self.details = str(self.outcome)
                    self.relevant_alignments = [target_alignment]

            elif len(interesting_indels) == 1:
                indel = interesting_indels[0]

                if len(self.non_pegRNA_SNVs) > 0:
                    self.subcategory = 'mismatches'
                else:
                    self.subcategory = 'clean'

                if indel.kind == 'D':
                    if self.non_primer_nts <= 10:
                        self.category = 'nonspecific amplification'
                        self.subcategory = 'primer dimer'
                        self.details = 'n/a'
                        self.relevant_alignments = self.uncategorized_relevant_alignments
                    else:
                        if indel == self.target_info.pegRNA_intended_deletion:
                            self.category = 'intended edit'
                            self.subcategory = 'deletion'
                            self.relevant_alignments = [target_alignment] + self.pegRNA_extension_als_list

                        else:
                            self.category = 'deletion'
                            self.relevant_alignments = [target_alignment]

                        self.outcome = DeletionOutcome(indel)
                        self.details = str(self.outcome)

                elif indel.kind == 'I':
                    self.category = 'insertion'
                    self.outcome = InsertionOutcome(indel)
                    self.details = str(self.outcome)
                    self.relevant_alignments = [target_alignment]

            else: # more than one indel
                if self.non_primer_nts <= 50:
                    self.category = 'nonspecific amplification'
                    self.subcategory = 'unknown'
                    self.details = 'n/a'
                    self.relevant_alignments = self.uncategorized_relevant_alignments

                else:
                    self.category = 'uncategorized'
                    self.subcategory = 'uncategorized'
                    self.details = 'n/a'
                    self.relevant_alignments = self.uncategorized_relevant_alignments

        elif self.duplication_covers_whole_read:
            subcategory, ref_junctions, indels, als_with_donor_SNVs, merged_als = self.duplication
            self.outcome = DuplicationOutcome(ref_junctions)

            self.category = 'duplication'

            self.subcategory = subcategory
            self.details = str(self.outcome)
            self.relevant_alignments = merged_als

        elif len(self.has_any_pegRNA_extension_al) > 0:
            if self.is_intended_replacement:
                self.category = 'intended edit'
                self.subcategory = self.is_intended_replacement
                self.outcome = Outcome('n/a')
                self.relevant_alignments = self.target_edge_alignments_list + self.pegRNA_extension_als_list

            else:
                self.category = 'unintended annealing of RT\'ed sequence'
                if len(self.has_any_pegRNA_extension_al) == 1:
                    side = sorted(self.has_any_pegRNA_extension_al)[0]
                    self.subcategory = f'{side} pegRNA'
                elif len(self.has_any_pegRNA_extension_al) == 2:
                    self.subcategory = f'both pegRNAs'
                else:
                    raise ValueError(len(self.has_any_pegRNA_extension_al))

                self.outcome = Outcome('n/a')
                self.relevant_alignments = self.target_edge_alignments_list + self.pegRNA_extension_als_list

        elif len(self.has_any_flipped_pegRNA_al) > 0:
            self.category = 'flipped pegRNA incorporation'
            if len(self.has_any_flipped_pegRNA_al) == 1:
                side = sorted(self.has_any_flipped_pegRNA_al)[0]
                self.subcategory = f'{side} pegRNA'
            elif len(self.has_any_flipped_pegRNA_al) == 2:
                self.subcategory = f'both pegRNAs'
            else:
                raise ValueError(len(self.has_any_flipped_pegRNA_al))

            self.outcome = Outcome('n/a')
            self.relevant_alignments = self.target_edge_alignments_list + self.flipped_pegRNA_als['left'] + self.flipped_pegRNA_als['right']

        elif self.non_primer_nts <= 50:
            self.category = 'nonspecific amplification'
            self.subcategory = 'unknown'
            self.details = 'n/a'
            self.relevant_alignments = self.uncategorized_relevant_alignments

        elif self.nonspecific_amplification:
            self.category = 'nonspecific amplification'
            organism, _ = self.target_info.remove_organism_from_alignment(self.nonspecific_amplification[0])
            self.subcategory = organism
            self.details = 'n/a'
            self.relevant_alignments = self.target_edge_alignments_list + self.nonspecific_amplification

        else:
            self.category = 'uncategorized'
            self.subcategory = 'uncategorized'
            self.details = 'n/a'

            self.relevant_alignments = self.uncategorized_relevant_alignments

        self.relevant_alignments = sam.make_nonredundant(self.relevant_alignments)

        if self.outcome is not None:
            # Translate positions to be relative to a registered anchor
            # on the target sequence.
            self.details = str(self.outcome.perform_anchor_shift(self.target_info.anchor))

        self.categorized = True

        return self.category, self.subcategory, self.details, self.outcome

    @memoized_property
    def manual_anchors(self):
        ''' Anchors for drawing knock-knock ref-centric diagrams with overlap in pegRNA aligned.
        '''
        ti = self.target_info

        manual_anchors = {}

        if ti.pegRNA_names is None:
            return manual_anchors

        overlap_feature = ti.features.get((ti.pegRNA_names[0], 'overlap'))
        if overlap_feature is not None:
            overlap_length = len(ti.features[ti.pegRNA_names[0], 'overlap'])

            overlap_offset_to_qs = defaultdict(dict)

            for side, expected_strand in [('left', '-'), ('right', '+')]:
                pegRNA_name = ti.pegRNA_names_by_side_of_read[side]
                
                pegRNA_als = [al for al in self.pegRNA_alignments[pegRNA_name] if sam.get_strand(al) == expected_strand]

                if len(pegRNA_als) == 0:
                    continue

                def priority_key(al):
                    is_extension_al = (al == self.pegRNA_extension_als['left']) or (al == self.pegRNA_extension_als['right'])
                    overlap_length = sam.feature_overlap_length(al, self.target_info.features[pegRNA_name, 'overlap'])
                    return is_extension_al, overlap_length
                
                pegRNA_als = sorted(pegRNA_als, key=priority_key)
                best_overlap_pegRNA_al = max(pegRNA_als, key=priority_key)
                
                overlap_offset_to_qs[side] = self.feature_offset_to_q(best_overlap_pegRNA_al, 'overlap')
                
            present_in_both = sorted(set(overlap_offset_to_qs['left']) & set(overlap_offset_to_qs['right']))
            present_in_either = sorted(set(overlap_offset_to_qs['left']) | set(overlap_offset_to_qs['right']))

            # If there is any offset present in both sides, use it as the anchor.
            # Otherwise, pick any offset present in either side arbitrarily.
            # If there is no such offset, don't make anchors for the pegRNAs.
            if overlap_length > 5 and present_in_either:
                if present_in_both:
                    anchor_offset = present_in_both[0]
                    qs = [overlap_offset_to_qs[side][anchor_offset] for side in ['left', 'right']] 
                    q = int(np.floor(np.mean(qs)))
                elif len(overlap_offset_to_qs['left']) > 0:
                    anchor_offset = sorted(overlap_offset_to_qs['left'])[0]
                    q = overlap_offset_to_qs['left'][anchor_offset]
                elif len(overlap_offset_to_qs['right']) > 0:
                    anchor_offset = sorted(overlap_offset_to_qs['right'])[0]
                    q = overlap_offset_to_qs['right'][anchor_offset]

                for side in ['left', 'right']:
                    pegRNA_name = ti.pegRNA_names_by_side_of_read[side]
                    ref_p = ti.feature_offset_to_ref_p(pegRNA_name, 'overlap')[anchor_offset]
                    manual_anchors[pegRNA_name] = (q, ref_p)
                
        return manual_anchors

    def plot(self, relevant=True, **manual_diagram_kwargs):
        if not self.categorized:
            self.categorize()

        ti = self.target_info

        color_overrides = {}
        if ti.primer_names is not None:
            for primer_name in ti.primer_names:
                color_overrides[primer_name] = 'lightgrey'

        pegRNA_names = ti.pegRNA_names
        if pegRNA_names is None:
            pegRNA_names = []
        else:
            for i, pegRNA_name in enumerate(pegRNA_names):
                color = f'C{i + 2}'
                light_color = hits.visualize.apply_alpha(color, 0.5)
                color_overrides[pegRNA_name] = color
                color_overrides[pegRNA_name, 'protospacer'] = light_color
                ps_name = knock_knock.pegRNAs.protospacer_name(pegRNA_name)
                color_overrides[ps_name] = light_color

                PAM_name = f'{ps_name}_PAM'
                color_overrides[PAM_name] = color

        features_to_show = {*ti.features_to_show}
        features_to_show.update(sorted(ti.PAM_features))
        label_offsets = {feature_name: 1 for _, feature_name in ti.PAM_features}
        label_overrides = {name: 'protospacer' for name in ti.sgRNAs}
        feature_heights = {}

        for deletion in self.target_info.pegRNA_programmed_deletions:
            label_offsets[deletion.ID] = 2
            label_overrides[deletion.ID] = 'intended deletion'
            feature_heights[deletion.ID] = 0.5

        if self.target_info.integrase_sites:
            suffixes = [
                'attP_left',
                'attP_right',
                'attB_left',
                'attB_right',
            ]

            for _, name in self.target_info.integrase_sites:
                for suffix in suffixes:
                    if name.endswith(suffix):
                        label_overrides[name] = '\n'.join(suffix.split('_'))
            
            label_offsets['RTT'] = 2

            for ref_name, name in self.target_info.integrase_sites:
                if 'left' in name or 'right' in name:
                    features_to_show.add((ref_name, name))

        if 'features_to_show' in manual_diagram_kwargs:
            features_to_show.update(manual_diagram_kwargs.pop('features_to_show'))

        if 'color_overrides' in manual_diagram_kwargs:
            color_overrides.update(manual_diagram_kwargs.pop('color_overrides'))

        if 'label_overrides' in manual_diagram_kwargs:
            label_overrides.update(manual_diagram_kwargs.pop('label_overrides'))

        refs_to_draw= {ti.target, *pegRNA_names}
        if 'refs_to_draw' in manual_diagram_kwargs:
            refs_to_draw.update(manual_diagram_kwargs.pop('refs_to_draw'))

        diagram_kwargs = dict(
            draw_sequence=True,
            flip_target=ti.sequencing_direction == '-',
            split_at_indels=True,
            label_offsets=label_offsets,
            features_to_show=features_to_show,
            manual_anchors=self.manual_anchors,
            refs_to_draw=refs_to_draw,
            label_overrides=label_overrides,
            inferred_amplicon_length=self.inferred_amplicon_length,
            center_on_primers=True,
            color_overrides=color_overrides,
            feature_heights=feature_heights,
        )

        diagram_kwargs.update(**manual_diagram_kwargs)

        if relevant:
            als_to_plot = self.relevant_alignments
        else:
            als_to_plot = self.uncategorized_relevant_alignments

        diagram = knock_knock.visualize.ReadDiagram(als_to_plot,
                                                    ti,
                                                    **diagram_kwargs,
                                                   )

        # Note that diagram.alignments may be different than als_to_plot
        # due to application of parsimony.

        # Draw the pegRNAs.
        if any(al.reference_name in pegRNA_names for al in diagram.alignments):
            ref_ys = {}
            ref_ys['left'] = diagram.max_y + diagram.target_and_donor_y_gap
            ref_ys['right'] = ref_ys['left'] + 7 * diagram.gap_between_als

            # To ensure that features on pegRNAs that extend far to the right of
            # the read are plotted, temporarily make the x range very wide.
            old_min_x, old_max_x = diagram.min_x, diagram.max_x

            diagram.min_x = -1000
            diagram.max_x = 1000

            ref_p_to_xs = {}

            left_name = ti.pegRNA_names_by_side_of_read['left']
            left_visible = any(al.reference_name == left_name for al in diagram.alignments)

            right_name = ti.pegRNA_names_by_side_of_read['right']
            right_visible = any(al.reference_name == right_name for al in diagram.alignments)

            ref_p_to_xs['left'] = diagram.draw_reference(left_name, ref_ys['left'],
                                                         flip=True,
                                                         label_features=left_visible and (not right_visible),
                                                         visible=left_visible,
                                                        )

            diagram.max_x = max(old_max_x, ref_p_to_xs['left'](0))

            ref_p_to_xs['right'] = diagram.draw_reference(right_name, ref_ys['right'],
                                                          flip=False,
                                                          label_features=True,
                                                          visible=right_visible,
                                                         )

            diagram.min_x = min(old_min_x, ref_p_to_xs['right'](0))

            diagram.ax.set_xlim(diagram.min_x, diagram.max_x)

            if self.manual_anchors and (left_name, 'overlap') in ti.features:
                offset_to_ref_ps = ti.feature_offset_to_ref_p(left_name, 'overlap')
                overlap_xs = sorted([ref_p_to_xs['left'](offset_to_ref_ps[0]), ref_p_to_xs['left'](offset_to_ref_ps[max(offset_to_ref_ps)])])

                overlap_xs = knock_knock.visualize.adjust_edges(overlap_xs)

                overlap_color = ti.features[left_name, 'overlap'].attribute['color']
                    
                diagram.ax.fill_betweenx([ref_ys['left'], ref_ys['right'] + diagram.ref_line_width + diagram.feature_line_width],
                                         [overlap_xs[0], overlap_xs[0]],
                                         [overlap_xs[1], overlap_xs[1]],
                                         color=overlap_color,
                                         alpha=0.3,
                                         visible=left_visible and right_visible,
                                        )

                text_x = np.mean(overlap_xs)
                text_y = np.mean([ref_ys['left'] + diagram.feature_line_width, ref_ys['right']])
                diagram.ax.annotate('overlap',
                                    xy=(text_x, text_y),
                                    color=overlap_color,
                                    ha='center',
                                    va='center',
                                    size=diagram.font_sizes['feature_label'],
                                    weight='bold',
                                    visible=left_visible and right_visible,
                                   )

            diagram.update_size()

        return diagram